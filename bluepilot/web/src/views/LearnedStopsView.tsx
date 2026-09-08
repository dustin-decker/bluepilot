import { useEffect, useState } from 'react'
import { Header } from '@/components/layout/Header'
import { VideoPlayer } from '@/components/video/VideoPlayer'
import { routesAPI } from '@/services/api'
import type { DeviceStatus, RouteDetails } from '@/types'
import './LearnedStopsView.css'

type Observation = { id: string; drive: string; utc: number; lat: number; lon: number; bearing: number | null;
  accuracy: number | null; reasons: string[]; excluded: boolean; path: number[][]; source: string; manual: boolean;
  evidence: { file: string; camera: string; offset: number; segment: number }[] }
type Stop = { id: string; vehicle: string; confirmed: string; disabled: boolean; visits: number; ready: boolean; qualified: boolean;
  status: string; accuracy_ok: boolean; spread: number; position: Observation; observations: Observation[];
  map_evidence?: { node: number; attribution: string } | null }
type Library = { mode: string; controlValidated: boolean; stops: Stop[] }

export function LearnedStopsView({ deviceStatus = 'checking' }: { deviceStatus?: DeviceStatus }) {
  const [library, setLibrary] = useState<Library | null>(null)
  const [selected, select] = useState<string | null>(null)
  const [observation, chooseObservation] = useState<string | null>(null)
  const [frame, chooseFrame] = useState(0)
  const [filter, setFilter] = useState('review')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [video, setVideo] = useState<RouteDetails | null>(null)
  const parked = deviceStatus === 'online'
  const stop = library?.stops.find(s => s.id === selected)
  const obs = stop?.observations.find(o => o.id === observation) ?? stop?.position
  const evidence = obs?.evidence ?? []
  const load = async () => {
    try {
      const response = await fetch('/api/learned-stops')
      const data = await response.json()
      if (!response.ok) throw new Error(data.error ?? 'Could not load stops')
      setLibrary(data)
      return data as Library
    } catch (e) { setError(String(e)) }
  }
  useEffect(() => { if (parked) void load() }, [parked])
  const send = async (endpoint: string, body: object) => {
    setBusy(true); setError('')
    try {
      const response = await fetch(`/api/learned-stops/${endpoint}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error ?? 'Could not save changes')
      return await load()
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  const review = async (action: string, reason?: string) => {
    const data = await send('review', { stop: selected, observation: obs?.id, action, reason })
    if (!data || !['exclude', 'confirm', 'approve', 'disable'].includes(action)) return
    const remaining = data.stops.filter(s => filter === 'all' || s.status === filter)
    const current = remaining.find(s => s.id === selected)
    const pending = action === 'exclude' && current?.observations.find(o => !o.excluded)
    const index = data.stops.findIndex(s => s.id === selected)
    const next = pending ? current : data.stops.slice(index + 1).concat(data.stops.slice(0, index))
      .find(s => remaining.includes(s))
    select(next?.id ?? null); chooseObservation(pending ? pending.id : null); chooseFrame(0)
  }
  if (video && parked) return <VideoPlayer route={video} initialSegment={evidence[0]?.segment ?? Number(obs?.source.split('--')[2] || 0)} onClose={() => setVideo(null)} />
  return <>
    <Header deviceStatus={deviceStatus} subtitle="Learned Stops" />
    <main className="learned-stops">
      <h1>Learned Stops</h1>
      <p>Learn your approaches. Review the evidence. Stop assistance begins in Observe mode.</p>
      <p>Stops flagged for a nearby lead skip the review queue. Excluded observations remain under All stops or Excluded.</p>
      {!parked && <p role="status">Park and connect to your device to review or change learned stops.</p>}
      {error && <p className="stop-error" role="alert">{error}</p>}
      <fieldset disabled={!parked || busy}>
        <legend>Mode</legend>
        <div className="stop-actions">{['off', 'observe', 'control'].map(mode => <button key={mode}
          aria-pressed={library?.mode === mode} disabled={mode === 'control' && !library?.controlValidated}
          onClick={() => { if (mode !== 'control' || window.confirm('Assist at qualified stops up to 70 mph. Coverage is incomplete. Check traffic and press the accelerator to depart. Enable Control?')) void send('mode', { mode }) }}>
          {mode.charAt(0).toUpperCase() + mode.slice(1)}</button>)}</div>
        <p>{library?.mode === 'off' ? 'Off retains saved locations.' : library?.mode === 'control' ? 'Assistance applies only at qualified stops. Check traffic and press the accelerator to depart.' : 'Observe learns and previews stops. You control braking.'}</p>
        {!library?.controlValidated && <p>Control is unavailable pending simulation and physical stopping validation.</p>}
      </fieldset>
      {parked && library && <div className="stop-layout">
        <aside>
          <label>Show <select value={filter} onChange={e => setFilter(e.target.value)}>
            <option value="all">All stops</option><option value="review">Needs review</option><option value="learning">Learning</option>
            <option value="approved">Approved</option><option value="disabled">Disabled</option><option value="excluded">Excluded</option>
          </select></label>
          {!library.stops.length && <p>No learned stops yet. Enable Observe and drive normally. Traffic queues and maneuvers do not count as usable visits.</p>}
          {library.stops.length > 0 && !library.stops.some(s => filter === 'all' || s.status === filter) && <p>No approaches in this queue.</p>}
          {library.stops.filter(s => filter === 'all' || s.status === filter).map(s => <button className="stop-row" key={s.id}
            aria-pressed={selected === s.id} onClick={() => { select(s.id); chooseObservation(null); chooseFrame(0) }}>
            {s.position.evidence[0] && <img className="stop-thumbnail" loading="lazy" alt="" src={`/api/learned-stops/evidence/${encodeURIComponent(s.position.evidence[0].file)}`} />}
            <strong>{s.position.bearing === null ? 'Direction unknown' : `${Math.round(s.position.bearing)}° approach`}</strong>
            <span>{s.position.lat.toFixed(5)}, {s.position.lon.toFixed(5)}</span>
            <span>{s.status} · {s.visits} of 3 independent drives</span>
            <small>{new Date(s.observations[s.observations.length - 1].utc * 1000).toLocaleString()}</small>
          </button>)}
        </aside>
        {stop && obs ? <section aria-label="Stop review">
          <h2>Approach evidence</h2>
          {evidence.length ? <>
            <img className="stop-frame" src={`/api/learned-stops/evidence/${encodeURIComponent(evidence[Math.min(frame, evidence.length - 1)].file)}`} alt={`${evidence[Math.min(frame, evidence.length - 1)].camera} view of the recorded approach`} />
            <div className="stop-actions">{evidence.map((e, i) => <button key={e.file} aria-pressed={frame === i} onClick={() => chooseFrame(i)}>{e.camera} · {e.offset}s</button>)}</div>
          </> : <p className="stop-empty">No saved frames for this observation. The location is not proof that a stop sign exists.</p>}
          {obs.source && <button disabled={busy} onClick={async () => {
            setBusy(true)
            try { setVideo(await routesAPI.getOne(obs.source.split('--').slice(0, 2).join('--'))) }
            catch { setError('Route video is unavailable. Saved frames remain available if captured earlier.') }
            finally { setBusy(false) }
          }}>Play approach segment</button>}
          <p>Playback starts at the retained segment boundary. Saved frames mark the exact sampled times.</p>
          <ApproachPath observation={obs} />
          <dl>
            <dt>Sign evidence</dt><dd>{stop.confirmed || 'Needs confirmation for this approach'}</dd>
            <dt>Map corroboration</dt><dd>{stop.map_evidence ? <a href={`https://www.openstreetmap.org/node/${stop.map_evidence.node}`} target="_blank" rel="noreferrer">Directional stop · © OpenStreetMap contributors</a> : 'No unambiguous cached map evidence. Review the camera frames.'}</dd>
            <dt>Usable visits</dt><dd>{stop.visits} of 3 independent drives</dd>
            <dt>Position</dt><dd>{obs.accuracy === null ? 'Accuracy unavailable' : `Estimated uncertainty: ${obs.accuracy.toFixed(1)} m`}</dd>
            <dt>Stopping consistency</dt><dd>{stop.spread.toFixed(1)} m observed spread</dd>
            <dt>Control readiness</dt><dd>{stop.ready ? 'Location approved; global mode still applies' : stop.qualified ? 'Ready for your approval' : 'More eligible observations, sign confirmation or better positioning required'}</dd>
          </dl>
          <fieldset disabled={busy || !parked}>
            <legend>Review this approach</legend>
            <div className="stop-actions">
              <button onClick={() => { if (window.confirm('Confirm a stop sign applies to this direction of travel? This does not bypass visit or positioning requirements.')) void review('confirm') }}>Confirm sign for this approach</button>
              <button disabled={obs.reasons.length > 0} onClick={() => { if (window.confirm('Use this recorded stopping position as the reference? The image itself is not a surveyed stop line.')) void review('reference') }}>Use this stopping reference</button>
              <button onClick={() => void review(stop.disabled ? 'enable' : 'disable')}>{stop.disabled ? 'Re-enable review' : 'Disable approach'}</button>
              <button disabled={!stop.qualified || stop.ready} onClick={() => { if (window.confirm('Approve assistance for this recorded approach when Control becomes available? Continue supervising and check traffic before departure.')) void review('approve') }}>Approve qualified approach</button>
              <button disabled={!evidence.length} onClick={() => { if (window.confirm('Delete this observation’s saved images to free space? Back them up first if needed. The observation remains and these images will not be captured again automatically.')) void send('evidence/clear', { observation: obs.id }) }}>Delete saved images</button>
            </div>
            <div role="group" aria-label="Exclude observation">
              <p>Exclude observation</p>
              <div className="stop-actions">{[['traffic_light', 'Traffic light'], ['queue', 'Queue'], ['maneuver', 'Parking or maneuver'],
                ['wrong_approach', 'Wrong approach'], ['unclear', 'Unclear']].map(([reason, label]) =>
                <button key={reason} disabled={obs.excluded} onClick={() => void review('exclude', reason)}>{label}</button>)}</div>
            </div>
          </fieldset>
          <h3>Observation history</h3>
          {stop.observations.map(o => <button className="stop-row" key={o.id} aria-pressed={obs.id === o.id} onClick={() => { chooseObservation(o.id); chooseFrame(0) }}>
            <strong>{new Date(o.utc * 1000).toLocaleString()}</strong><span>{o.manual ? 'Manual' : 'Automatic or unverified'} · {o.reasons.length ? o.reasons.join(', ').replace(/_/g, ' ') : 'Eligible observation'}</span>
            <small>Drive {o.drive} · {o.source}</small>
          </button>)}
        </section> : <section><p>Select an approach to review its evidence and stopping positions.</p></section>}
      </div>}
      <details><summary>Backup and import</summary>
        <p>Exports retain sign confirmations, approvals, stopping references and exclusions. Import restores those decisions; readiness is recalculated from the evidence and the current mode stays unchanged.</p>
        <p>Saved images have a 512 MiB budget. Back up and delete older images when space is needed.</p>
        {parked && <a href="/api/learned-stops/export" download="learned-stops.json">Export evidence</a>}
        {parked && <p><a href="/api/learned-stops/backup" download="learned-stops.zip">Back up database and saved frames</a></p>}
        <label>Import evidence <input type="file" accept="application/json" disabled={!parked || busy} onChange={async e => {
          const file = e.target.files?.[0]; if (!file) return
          if (file.size > 4 * 1024 * 1024) { setError('Import must be smaller than 4 MiB'); return }
          try { await send('import', JSON.parse(await file.text())) } catch { setError('Invalid JSON file') }
        }} /></label>
      </details>
    </main>
  </>
}

function ApproachPath({ observation }: { observation: Observation }) {
  if (observation.path.length < 2) return <p>Approach trace unavailable.</p>
  const points = observation.path.map(p => [(p[1] - observation.lon) * Math.cos(observation.lat * Math.PI / 180), -(p[0] - observation.lat)])
  const extent = Math.max(0.0001, ...points.flat().map(Math.abs))
  const project = (p: number[]) => `${150 + p[0] / extent * 130},${150 + p[1] / extent * 130}`
  return <figure><svg className="stop-trace" viewBox="0 0 300 300" role="img" aria-label="Recorded approach trace; north is up. Circle marks the observed vehicle stopping position.">
    <text x="145" y="16" fill="currentColor">N</text><polyline points={points.map(project).join(' ')} fill="none" stroke="#2196f3" strokeWidth="3" />
    <circle cx="150" cy="150" r="7" fill="#ff9800" />
  </svg><figcaption>Recorded approach · circle = vehicle stopping position, not sign location</figcaption></figure>
}
