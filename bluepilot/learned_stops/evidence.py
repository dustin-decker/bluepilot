"""Save route frames while offroad. Video indices, never assumed frame rates."""
from __future__ import annotations

from typing import Any
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import time

from .store import Store

EVIDENCE_BUDGET = 512 * 1024 * 1024


@contextmanager
def evidence_lock(store: Store) -> Iterator[None]:
  with (store.root / '.evidence.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
      yield
    finally:
      fcntl.flock(lock, fcntl.LOCK_UN)


def capture(frame: dict[str, Any], log_root: str | Path, output: Path, available_bytes: int = EVIDENCE_BUDGET) -> dict[str, Any]:
  import av
  from openpilot.tools.lib.vidindex import hevc_index

  route = frame['route']
  if not isinstance(route, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', route):
    raise ValueError('Invalid route identifier')
  segment, index = frame['segment'], frame['index']
  if type(segment) is not int or type(index) is not int or not 0 <= segment < 100000 or not 0 <= index < 10000:
    raise ValueError('Invalid video position')
  camera = {'front': 'fcamera.hevc', 'wide': 'ecamera.hevc'}[frame['camera']]
  video = Path(log_root) / f'{route}--{segment}' / camera
  types, length, prefix = hevc_index(str(video))
  if index >= len(types) or any(t[0] == 0 for t in types):
    raise ValueError('Missing frame or unsupported reordered video')
  start = index
  while start > 0 and types[start][0] != 2:
    start -= 1
  end = index + 1
  while end < len(types) and types[end][0] != 2:
    end += 1
  with video.open('rb') as stream:
    stream.seek(types[start][1])
    raw = prefix + stream.read((types[end][1] if end < len(types) else length) - types[start][1])
  with av.open(io.BytesIO(raw), format='hevc') as container:
    for number, decoded in enumerate(container.decode(video=0)):
      if number == index - start:
        image = decoded.to_image()
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=85)
        raw = buffer.getvalue()
        if len(raw) > available_bytes:
          raise ValueError('Saved-frame budget reached (512 MiB); export and remove old evidence before capturing more')
        name = hashlib.sha256(raw).hexdigest() + '.jpg'
        output.mkdir(parents=True, exist_ok=True)
        temporary = output / (name + '.tmp')
        temporary.write_bytes(raw)
        temporary.replace(output / name)
        return dict(frame, file=name, width=image.width, height=image.height)
  raise ValueError('Decoder did not return requested frame')


def save_evidence(store: Store, observation_id: str, frame: dict[str, Any]) -> None:
  with closing(store.connect()) as db, db:
    row = db.execute('SELECT data FROM observations WHERE id=?', (observation_id,)).fetchone()
    if row is None:
      return
    obs = json.loads(row[0])
    if obs.get('evidence_removed'):
      return
    if not any(e['camera'] == frame['camera'] and e['offset'] == frame['offset'] for e in obs['evidence']):
      obs['evidence'].append(frame)
    db.execute('UPDATE observations SET data=? WHERE id=?', (json.dumps(obs, allow_nan=False), observation_id))


def remove_evidence(store: Store, observation_id: str | None, onroad: Callable[[], bool] = lambda: False) -> None:
  """Forget selected saved images, retaining the observation and any shared images."""
  with evidence_lock(store):
    if onroad():
      raise ValueError('Vehicle started driving; image removal cancelled')
    with closing(store.connect()) as db, db:
      row = db.execute('SELECT data FROM observations WHERE id=?', (observation_id,)).fetchone()
      if row is None:
        raise ValueError('Unknown observation')
      obs = json.loads(row[0])
      removed = {e['file'] for e in obs['evidence']}
      obs.update(evidence=[], frames=[], evidence_removed=True)
      db.execute('UPDATE observations SET data=? WHERE id=?', (json.dumps(obs, allow_nan=False), observation_id))
      used = {e['file'] for row in db.execute('SELECT data FROM observations') for e in json.loads(row[0]).get('evidence', [])}
    for name in removed - used:
      if re.fullmatch(r'[a-f0-9-]{36,64}\.jpg', name):
        (store.root / 'evidence' / name).unlink(missing_ok=True)


def collect(store: Store, log_root: str | Path, onroad: Callable[[], bool] = lambda: False) -> list[dict[str, Any]]:
  """One bounded pass. A subsequent offroad cycle retries unavailable route video."""
  from av.error import FFmpegError
  from openpilot.tools.lib.vidindex import VideoFileInvalid
  failures: list[dict[str, Any]] = []
  remaining = EVIDENCE_BUDGET - sum(p.stat().st_size for p in (store.root / 'evidence').glob('*.jpg'))
  for stop in store.list():
    for obs in stop['observations']:
      if obs.get('evidence_removed'):
        continue
      for frame in obs.get('frames', []):
        if onroad():
          return failures
        if any(e['camera'] == frame['camera'] and e['offset'] == frame['offset'] for e in obs['evidence']):
          continue
        try:
          with evidence_lock(store):
            with closing(store.connect()) as db:
              current = json.loads(db.execute('SELECT data FROM observations WHERE id=?', (obs['id'],)).fetchone()[0])
            if current.get('evidence_removed'):
              break
            saved = capture(frame, log_root, store.root / 'evidence', available_bytes=remaining)
            remaining -= (store.root / 'evidence' / saved['file']).stat().st_size
            if onroad():
              return failures
            save_evidence(store, obs['id'], saved)
        except (OSError, ValueError, KeyError, IndexError, VideoFileInvalid, FFmpegError) as error:
          failures.append({'observation': obs['id'], 'error': str(error)})
          if remaining <= 0 or 'budget reached' in str(error):
            return failures
        time.sleep(0.1)
  return failures


def corroborate_cached_maps(store: Store) -> None:
  from .osm import corroborate
  # Maps are deliberately fetched explicitly by the offline tool, never while driving.
  maps = [json.loads(p.read_text()) for p in (store.root / 'osm').glob('*.json') if p.stat().st_size <= 8 * 1024 * 1024]
  for stop in store.list():
    results = [result for data in maps if (result := corroborate(stop['position'], data))]
    unique = {r['node']: r for r in results}
    if len(unique) != 1:
      continue
    with closing(store.connect()) as db, db:
      db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ('osm:' + stop['id'], json.dumps(next(iter(unique.values())))))


def main() -> None:
  from openpilot.common.params import Params
  from openpilot.common.swaglog import cloudlog
  from openpilot.system.hardware.hw import Paths

  params = Params()
  time.sleep(5)
  while not params.get_bool('IsOnroad'):
    try:
      store = Store()
      try:
        corroborate_cached_maps(store)
      except (ValueError, KeyError, OSError):
        cloudlog.exception('learned_stop_map_cache_invalid')
      errors = collect(store, Paths.log_root(), lambda: params.get_bool('IsOnroad'))
      if errors:
        cloudlog.warning({'event': 'learned_stop_evidence_unavailable', 'failures': errors[:20], 'count': len(errors)})
      break
    except sqlite3.Error:
      cloudlog.exception('learned_stop_storage_busy')
      time.sleep(30)
    except (ValueError, KeyError, OSError):
      cloudlog.exception('learned_stop_evidence_unavailable')
      break
  while True:
    time.sleep(30)


if __name__ == '__main__':
  main()
