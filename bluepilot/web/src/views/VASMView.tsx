import { type MouseEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Header } from '@/components/layout/Header'
import { Button } from '@/components/common'
import { paramsAPI } from '@/services/api'
import type { DeviceStatus } from '@/types'
import './VASMView.css'

type Side = 'left' | 'right'
type Point = [number, number]

interface VASMConfig {
  width: number
  height: number
  poly_left: Point[]
  poly_right: Point[]
}

interface VASMStatus {
  enabled: boolean
  leftActive: boolean
  rightActive: boolean
  leftConfidence: number
  rightConfidence: number
}

interface VASMViewProps {
  deviceStatus?: DeviceStatus
}

const drawPolygon = (
  ctx: CanvasRenderingContext2D,
  points: Point[],
  stroke: string,
  fill: string,
) => {
  if (!points.length) return
  ctx.beginPath()
  ctx.moveTo(points[0][0], points[0][1])
  points.slice(1).forEach(([x, y]) => ctx.lineTo(x, y))
  if (points.length >= 3) ctx.closePath()
  ctx.fillStyle = fill
  ctx.fill()
  ctx.lineWidth = Math.max(3, ctx.canvas.width / 500)
  ctx.strokeStyle = stroke
  ctx.stroke()
  points.forEach(([x, y], index) => {
    ctx.beginPath()
    ctx.arc(x, y, Math.max(5, ctx.canvas.width / 280), 0, Math.PI * 2)
    ctx.fillStyle = index === 0 ? '#ffffff' : stroke
    ctx.fill()
  })
}

export function VASMView({ deviceStatus = 'checking' }: VASMViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const redrawRef = useRef<() => void>(() => {})
  const [leftPoints, setLeftPoints] = useState<Point[]>([])
  const [rightPoints, setRightPoints] = useState<Point[]>([])
  const [annotating, setAnnotating] = useState<Side | null>(null)
  const [configExists, setConfigExists] = useState(false)
  const [loadingSnapshot, setLoadingSnapshot] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState<VASMStatus | null>(null)
  const [confidence, setConfidence] = useState(0.85)
  const [smoothing, setSmoothing] = useState(0.2)
  const offroad = deviceStatus !== 'onroad'

  const redraw = useCallback(() => {
    const canvas = canvasRef.current
    const image = imageRef.current
    if (!canvas || !image) return
    if (canvas.width !== image.naturalWidth || canvas.height !== image.naturalHeight) {
      canvas.width = image.naturalWidth
      canvas.height = image.naturalHeight
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
    drawPolygon(ctx, leftPoints, '#2196f3', 'rgba(33, 150, 243, 0.22)')
    drawPolygon(ctx, rightPoints, '#ff9800', 'rgba(255, 152, 0, 0.22)')
  }, [leftPoints, rightPoints])

  useEffect(() => {
    redrawRef.current = redraw
    redraw()
  }, [redraw])

  const loadSnapshot = useCallback(() => {
    if (!offroad) return
    setLoadingSnapshot(true)
    setError('')
    const image = new Image()
    image.onload = () => {
      imageRef.current = image
      setLoadingSnapshot(false)
      requestAnimationFrame(() => redrawRef.current())
    }
    image.onerror = () => {
      setLoadingSnapshot(false)
      setError('Unable to capture a driver-camera snapshot. Keep the device parked and retry.')
    }
    image.src = `/api/vasm/snapshot?t=${Date.now()}`
  }, [offroad])

  useEffect(() => {
    const load = async () => {
      try {
        const [configResponse, allParams] = await Promise.all([
          fetch('/api/vasm/config'),
          paramsAPI.getAll(),
        ])
        const payload = await configResponse.json()
        const config = (payload.config || {}) as Partial<VASMConfig>
        const left = Array.isArray(config.poly_left) ? config.poly_left : []
        const right = Array.isArray(config.poly_right) ? config.poly_right : []
        setLeftPoints(left)
        setRightPoints(right)
        setConfigExists(left.length >= 3 || right.length >= 3)
        setConfidence(Number(allParams.VASMConfidenceThreshold?.value ?? 0.85))
        setSmoothing(Number(allParams.VASMSmoothSeconds?.value ?? 0.2))
      } catch {
        setError('Unable to load V-ASM configuration.')
      }
    }
    load()
    loadSnapshot()
  }, [loadSnapshot])

  useEffect(() => {
    const updateStatus = async () => {
      try {
        const response = await fetch('/api/vasm/status')
        if (response.ok) setStatus(await response.json())
      } catch {
        // Status is informational; keep the last successful value.
      }
    }
    updateStatus()
    const interval = window.setInterval(updateStatus, 3000)
    return () => window.clearInterval(interval)
  }, [])

  const canvasPoint = (event: MouseEvent<HTMLCanvasElement>): Point | null => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    return [
      Math.round((event.clientX - rect.left) * canvas.width / rect.width),
      Math.round((event.clientY - rect.top) * canvas.height / rect.height),
    ]
  }

  const addPoint = (event: MouseEvent<HTMLCanvasElement>) => {
    if (!annotating || !offroad) return
    const point = canvasPoint(event)
    if (!point) return
    if (annotating === 'left') setLeftPoints((points) => [...points, point])
    else setRightPoints((points) => [...points, point])
  }

  const undoPoint = (event: MouseEvent<HTMLCanvasElement>) => {
    event.preventDefault()
    if (annotating === 'left') setLeftPoints((points) => points.slice(0, -1))
    if (annotating === 'right') setRightPoints((points) => points.slice(0, -1))
  }

  const save = async () => {
    const canvas = canvasRef.current
    if (!canvas || (leftPoints.length < 3 && rightPoints.length < 3)) return
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const response = await fetch('/api/vasm/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          width: canvas.width,
          height: canvas.height,
          poly_left: leftPoints.length >= 3 ? leftPoints : [],
          poly_right: rightPoints.length >= 3 ? rightPoints : [],
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.error || 'Unable to save annotations')
      setConfigExists(true)
      setAnnotating(null)
      setMessage('Annotations saved and V-ASM enabled.')
      setStatus((current) => current ? { ...current, enabled: true } : current)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to save annotations')
    } finally {
      setSaving(false)
    }
  }

  const deleteConfig = async () => {
    if (!window.confirm('Delete both annotations and disable V-ASM?')) return
    const response = await fetch('/api/vasm/config', { method: 'DELETE' })
    if (!response.ok) {
      setError('Unable to delete V-ASM configuration.')
      return
    }
    setLeftPoints([])
    setRightPoints([])
    setConfigExists(false)
    setAnnotating(null)
    setMessage('Annotations cleared and V-ASM disabled.')
    setStatus((current) => current ? { ...current, enabled: false } : current)
  }

  const updateTuning = async (key: string, value: number) => {
    try {
      await paramsAPI.update(key, value)
      setMessage('Sensitivity setting updated.')
    } catch {
      setError('Unable to update sensitivity setting.')
    }
  }

  return (
    <>
      <Header deviceStatus={deviceStatus} subtitle="Vision adjacent spot monitoring" />
      <main className="vasm-page">
        <section className="vasm-card vasm-intro">
          <h2>Vision Adjacent Spot Monitoring</h2>
          <p>
            V-ASM uses the driver camera to supplement factory blind-spot monitoring. It can miss
            vehicles or report false positives—always check before changing lanes.
          </p>
          <ul>
            <li>Trace the visible left and right side-window glass from the snapshot.</li>
            <li>Exclude pillars, door frames, and interior trim; include mirrors visible through glass.</li>
            <li>Right-click the image to undo the most recent point.</li>
          </ul>
        </section>

        {!offroad && <div className="vasm-banner warning">Configuration is locked while driving.</div>}
        {error && <div className="vasm-banner error">{error}</div>}
        {message && <div className="vasm-banner success">{message}</div>}

        <section className="vasm-card">
          <div className="vasm-toolbar">
            <Button
              variant={annotating === 'left' ? 'primary' : 'secondary'}
              onClick={() => setAnnotating('left')}
              disabled={!offroad || !imageRef.current}
            >
              Trace Left
            </Button>
            <Button
              variant={annotating === 'right' ? 'primary' : 'secondary'}
              onClick={() => setAnnotating('right')}
              disabled={!offroad || !imageRef.current}
            >
              Trace Right
            </Button>
            <Button variant="success" onClick={save} loading={saving} disabled={!offroad || (leftPoints.length < 3 && rightPoints.length < 3)}>
              Save & Enable
            </Button>
            <Button variant="secondary" onClick={loadSnapshot} loading={loadingSnapshot} disabled={!offroad}>
              New Snapshot
            </Button>
            {configExists && <Button variant="danger" onClick={deleteConfig} disabled={!offroad}>Delete & Disable</Button>}
          </div>
          <div className="vasm-point-counts">
            <span className="left">Left: {leftPoints.length} points</span>
            <span className="right">Right: {rightPoints.length} points</span>
            {annotating && <span>Tracing {annotating}; select another side or save when finished.</span>}
          </div>
          <div className="vasm-canvas-wrap">
            {loadingSnapshot && <div className="vasm-canvas-placeholder">Capturing driver camera…</div>}
            <canvas
              ref={canvasRef}
              onClick={addPoint}
              onContextMenu={undoPoint}
              aria-label="Driver camera V-ASM annotation canvas"
            />
          </div>
        </section>

        <section className="vasm-card vasm-tuning">
          <h3>Sensitivity</h3>
          <label>
            <span>Confidence threshold: {confidence.toFixed(2)}</span>
            <input
              type="range"
              min="0.25"
              max="1"
              step="0.01"
              value={confidence}
              disabled={!offroad}
              onChange={(event) => setConfidence(Number(event.target.value))}
              onMouseUp={() => updateTuning('VASMConfidenceThreshold', confidence)}
              onTouchEnd={() => updateTuning('VASMConfidenceThreshold', confidence)}
            />
          </label>
          <label>
            <span>Smoothing duration: {smoothing.toFixed(2)} s</span>
            <input
              type="range"
              min="0.1"
              max="0.5"
              step="0.01"
              value={smoothing}
              disabled={!offroad}
              onChange={(event) => setSmoothing(Number(event.target.value))}
              onMouseUp={() => updateTuning('VASMSmoothSeconds', smoothing)}
              onTouchEnd={() => updateTuning('VASMSmoothSeconds', smoothing)}
            />
          </label>
        </section>

        <section className="vasm-card">
          <h3>Current status</h3>
          <div className="vasm-status-grid">
            <div><span>Daemon</span><strong className={status?.enabled ? 'active' : ''}>{status?.enabled ? 'Enabled' : 'Disabled'}</strong></div>
            <div><span>Left</span><strong className={status?.leftActive ? 'active' : ''}>{status?.leftActive ? 'Detected' : 'Clear'}</strong></div>
            <div><span>Right</span><strong className={status?.rightActive ? 'active' : ''}>{status?.rightActive ? 'Detected' : 'Clear'}</strong></div>
            <div><span>Confidence</span><strong>{(status?.leftConfidence ?? 0).toFixed(3)} / {(status?.rightConfidence ?? 0).toFixed(3)}</strong></div>
          </div>
        </section>
      </main>
    </>
  )
}
