"""Offroad portal endpoints, shared by the portal and its HTTP tests."""
from __future__ import annotations

from typing import Any
from collections.abc import Callable
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import zipfile
import shutil
import uuid

from .core import CONTROL_VALIDATED
from .store import Store


def handle(handler: Any, path: str, method: str, onroad: Callable[[], bool], store: Store | None = None) -> bool:
  if not (path == "/api/learned-stops" or path.startswith("/api/learned-stops/")):
    return False
  if onroad():
    handler.send_json_response({"error": "Park to review or modify learned stops"}, 403)
    return True
  try:
    store = store or Store()
    if method == "GET":
      if path == "/api/learned-stops/backup":
        from .evidence import evidence_lock
        with tempfile.TemporaryDirectory(prefix='backup-', dir=store.root) as directory:
          with evidence_lock(store):
            frames = [p for p in (store.root / 'evidence').glob('*.jpg') if p.is_file() and not p.is_symlink()]
            needed = sum(p.stat().st_size for p in frames) + 2 * store.path.stat().st_size + 16 * 1024 * 1024
            if shutil.disk_usage(store.root).free < needed:
              raise ValueError('Not enough space to prepare a complete backup; export JSON or free space first')
            snapshot = Path(directory) / 'stops.sqlite3'
            with closing(store.connect()) as source, closing(sqlite3.connect(snapshot)) as destination:
              source.backup(destination)
            archive = Path(directory) / 'learned-stops.zip'
            with zipfile.ZipFile(archive, 'w') as backup:
              backup.write(snapshot, 'stops.sqlite3', compress_type=zipfile.ZIP_DEFLATED)
              for frame in frames:
                if onroad():
                  raise ValueError('Vehicle started driving; backup cancelled')
                backup.write(frame, 'evidence/' + frame.name)
          handler.send_file_response(str(archive), 'application/zip', 'learned-stops.zip')
      elif path.startswith("/api/learned-stops/evidence/"):
        name = path.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[a-f0-9-]{36,64}\.jpg", name):
          raise ValueError("Invalid evidence name")
        handler.send_file_response(str(store.root / "evidence" / name), "image/jpeg")
      elif path == "/api/learned-stops/export":
        handler.send_json_response({"version": 1, "stops": store.list()})
      elif path == "/api/learned-stops":
        handler.send_json_response({"version": 1, "mode": store.mode(), "controlValidated": CONTROL_VALIDATED,
                                    "stops": store.list()})
      else:
        handler.send_json_response({"error": "Unknown learned-stop endpoint"}, 404)
    elif method == "POST":
      length = int(handler.headers.get("Content-Length", 0))
      if not 0 < length <= 4 * 1024 * 1024:
        raise ValueError("Request must be between 1 byte and 4 MiB")
      body = json.loads(handler.rfile.read(length))
      if not isinstance(body, dict):
        raise ValueError("Expected an object")
      # Recheck after reading the body: a slow upload may span going onroad.
      if onroad():
        handler.send_json_response({"error": "Vehicle started driving"}, 403)
        return True
      if path == "/api/learned-stops/mode":
        store.set_mode(body.get("mode"))
      elif path == "/api/learned-stops/review":
        store.review(body.get("stop"), body.get("action"), body.get("observation"), body.get("reason"))
      elif path == '/api/learned-stops/evidence/clear':
        from .evidence import remove_evidence
        remove_evidence(store, body.get('observation'), onroad)
      elif path == "/api/learned-stops/import":
        import_observations(store, body, onroad)
      else:
        raise ValueError("Unknown learned-stop endpoint")
      handler.send_json_response({"success": True})
    else:
      handler.send_json_response({"error": "Method not supported"}, 405)
  except (ValueError, TypeError, KeyError) as error:
    handler.send_json_response({"error": str(error)}, 400)
  except (OSError, sqlite3.Error):
    handler.send_json_response({'error': 'Stop storage unavailable; please retry while parked'}, 503)
  return True


def import_observations(store: Store, body: dict[str, Any], onroad: Callable[[], bool] = lambda: False) -> None:
  """Restore exported review decisions; recompute readiness from saved evidence."""
  if body.get("version") != 1 or not isinstance(body.get("stops"), list) or len(body["stops"]) > 1000:
    raise ValueError("Unsupported or oversized stop export")
  groups, identities, count = [], set(), 0
  for stop in body["stops"]:
    if not isinstance(stop, dict):
      raise ValueError("Invalid stop object")
    vehicle = stop["vehicle"]
    if not isinstance(vehicle, str) or not 1 <= len(vehicle) <= 256:
      raise ValueError("Invalid vehicle")
    for key in ('id', 'confirmed', 'reference'):
      value = stop.get(key)
      if value is not None and (not isinstance(value, str) or len(value) > 256):
        raise ValueError(f'Invalid {key}')
    for key in ('disabled', 'approved'):
      if key in stop and (type(stop[key]) not in (bool, int) or stop[key] not in (0, 1)):
        raise ValueError(f'Invalid {key}')
    if 'confirmed' in stop and not isinstance(stop['confirmed'], str):
      raise ValueError('Invalid confirmation')
    if not isinstance(stop.get("observations"), list) or not 1 <= len(stop["observations"]) <= 1000:
      raise ValueError("Invalid observations")
    observations, ids = [], set()
    for item in stop["observations"]:
      if not isinstance(item, dict):
        raise ValueError("Invalid observation object")
      external_id = item.get('id')
      if external_id is not None:
        if not isinstance(external_id, str) or not 1 <= len(external_id) <= 256 or external_id in ids:
          raise ValueError('Invalid or repeated observation ID')
        ids.add(external_id)
      allowed = ('drive', 'utc', 'lat', 'lon', 'bearing', 'accuracy', 'reasons', 'path', 'manual', 'model_stop', 'source', 't', 'excluded')
      obs = {key: item[key] for key in allowed if key in item}
      if 'excluded' in obs and type(obs['excluded']) is not bool:
        raise ValueError('Invalid exclusion flag')
      if not isinstance(obs.get('reasons'), list):
        raise ValueError('Invalid observation exclusions')
      obs['reasons'] = [reason for reason in obs['reasons'] if reason != 'imported_unverified']
      store.validate_observation(obs)
      identity = (vehicle, obs['drive'], obs['utc'])
      if identity in identities:
        raise ValueError('Repeated observation identity')
      identities.add(identity)
      observations.append((external_id, obs))
      count += 1
      if count > 1000:
        raise ValueError('Import at most 1000 observations per request')
    reference = stop.get('reference')
    if reference is not None and reference not in ids:
      raise ValueError('Stopping reference is missing from exported observations')
    if stop.get('approved') and (not stop.get('confirmed') or reference is None):
      raise ValueError('Approval requires exported sign confirmation and a stopping reference')
    if stop.get('approved') and any(o['reasons'] or o.get('excluded') for key, o in observations if key == reference):
      raise ValueError('Stopping reference must be an eligible observation')
    groups.append((stop, observations))

  # One transaction preserves all review/reference relationships, including retries.
  # Match exact observation identities, never transfer approvals by nearby GPS alone.
  with closing(store.connect()) as db, db:
    for stop, observations in groups:
      vehicle = stop['vehicle']
      existing = [db.execute('SELECT id,stop,data FROM observations WHERE vehicle=? AND drive=? AND utc=?',
                            (vehicle, obs['drive'], obs['utc'])).fetchone() for _, obs in observations]
      targets = {row['stop'] for row in existing if row is not None}
      named = db.execute('SELECT id,vehicle FROM stops WHERE id=?', (stop.get('id'),)).fetchone()
      if named:
        if named['vehicle'] != vehicle:
          raise ValueError('Exported stop ID belongs to another vehicle')
        targets.add(named['id'])
      if len(targets) > 1:
        raise ValueError('Exported observations conflict with existing approach groups')
      stop_id = next(iter(targets), None) or stop.get('id') or str(uuid.uuid4())
      db.execute('INSERT OR IGNORE INTO stops(id,vehicle) VALUES (?,?)', (stop_id, vehicle))
      mapped = {}
      for (external_id, obs), row in zip(observations, existing, strict=True):
        if onroad():
          raise ValueError('Vehicle started; import cancelled without changes')
        if row:
          saved = json.loads(row['data'])
          if any(saved.get(key) != obs.get(key) for key in ('lat', 'lon', 'bearing', 'path')):
            raise ValueError('Exported observation conflicts with its saved position or approach')
          obs = {**saved, **obs, 'evidence': saved.get('evidence', [])}
          observation_id = row['id']
          db.execute('UPDATE observations SET data=? WHERE id=?', (json.dumps(obs, allow_nan=False), observation_id))
        else:
          observation_id = str(uuid.uuid4())
          db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?)',
                     (observation_id, stop_id, vehicle, obs['drive'], obs['utc'], json.dumps(obs, allow_nan=False)))
        if external_id is not None:
          mapped[external_id] = observation_id
      for key in ('confirmed', 'disabled', 'approved', 'reference'):
        if key in stop:
          value = mapped.get(stop[key]) if key == 'reference' else stop[key]
          db.execute(f'UPDATE stops SET {key}=? WHERE id=?', (value, stop_id))
    if onroad():
      raise ValueError('Vehicle started; import cancelled without changes')
