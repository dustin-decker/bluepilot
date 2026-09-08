"""Observe stops and publish cached targets. No network or planner-thread SQL."""
from __future__ import annotations

from typing import Any
from collections.abc import Iterator
import json
import math
import time
import uuid
import sqlite3
from contextlib import closing

from .core import Observer, Sample, match_path
from .types import Target
from .store import Store
from .telemetry import Telemetry


class StopMatcher:
  """Cache eligibility/bounds once; preserve the exact path projection for candidates."""
  def __init__(self, stops: list[dict[str, Any]]) -> None:
    self.candidates: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
    for stop in stops:
      path = stop['position']['path']
      if stop['disabled'] or (not stop['visits'] and not stop['confirmed']) or len(path) < 2:
        continue
      self.candidates.append((stop, (min(p[0] for p in path), max(p[0] for p in path),
                                     min(p[1] for p in path), max(p[1] for p in path))))

  def find(self, sample: Sample, heading: float | None) -> Target | None:
    if not sample.located() or heading is None:
      return None
    assert sample.lat is not None and sample.lon is not None
    xscale = 111320 * math.cos(math.radians(sample.lat))
    matches: list[Target] = []
    for stop, (south, north, west, east) in self.candidates:
      # A projection within a segment and <4m cross-track cannot be outside
      # its path's bounding rectangle expanded by 4m. This rejects no valid match.
      if ((south - sample.lat) * 111320 > 4 or (sample.lat - north) * 111320 > 4 or
          (west - sample.lon) * xscale > 4 or (sample.lon - east) * xscale > 4):
        continue
      p = stop['position']
      distance = match_path(sample, p['path'], heading)
      if distance is not None and 0 < distance <= 600:
        matches.append({'id': stop['id'], 'distance': distance, 'accuracy': p['accuracy'] or 1e6,
                        'ready': stop['ready'], 'visits': stop['visits'], 'confirmed': stop['confirmed']})
    matches.sort(key=lambda m: m['distance'])
    if len(matches) > 1 and matches[1]['distance'] - matches[0]['distance'] < 25:
      return None
    return matches[0] if matches else None


def find_target(sample: Sample, heading: float | None, stops: list[dict[str, Any]]) -> Target | None:
  """One-shot lookup for callers without a persistent library cache."""
  return StopMatcher(stops).find(sample, heading)


def message_batches(services: list[str]) -> Iterator[list[Any]]:
  """Drain full-rate bounded native queues at 20Hz, keeping brief input events."""
  import cereal.messaging as messaging
  from openpilot.common.realtime import Ratekeeper

  sockets = [messaging.sub_sock(name, conflate=False) for name in services]
  rate = Ratekeeper(20, print_delay_threshold=None)
  while True:
    rate.keep_time()
    # Match offline replay ordering. Do not conflate away short brake/lead events
    # or frame indices. Native queues are bounded; time gaps reset the observer.
    messages = [message for socket in sockets for message in messaging.drain_sock(socket, wait_for_one=False)]
    messages.sort(key=lambda message: message.logMonoTime)
    yield messages


def main() -> None:
  import cereal.messaging as messaging
  from cereal import car
  from openpilot.common.params import Params
  from openpilot.common.gps import get_gps_location_service
  from openpilot.common.swaglog import cloudlog

  params = Params()
  cp = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  store = Store()
  gps_service = get_gps_location_service(params)
  services = ["carState", "carControl", "radarState", "modelV2", "liveLocationKalman", gps_service,
              "roadEncodeIdx", "wideRoadEncodeIdx"]
  pm = messaging.PubMaster(["learnedStopsBP"])
  telemetry = Telemetry(gps_service)
  route = params.get("CurrentRoute") or ""
  if isinstance(route, bytes):
    route = route.decode()
  # Survive process/route restarts without manufacturing a new independent drive.
  with closing(store.connect()) as db, db:
    previous = db.execute("SELECT value FROM settings WHERE key='drive'").fetchone()
    try:
      previous = json.loads(previous[0]) if previous else {}
      if not isinstance(previous, dict) or type(previous.get('seen', 0)) not in (int, float):
        previous = {}
    except (ValueError, TypeError):
      previous = {}
    drive = previous.get("id") if 0 <= time.time() - previous.get("seen", 0) < 1200 else None  # noqa: TID251 -- persisted across reboot
    drive = drive or str(uuid.uuid4())
  observer = Observer(drive)
  last_refresh = last_publish = 0.0
  matcher, mode = StopMatcher([]), "off"
  targets_loaded = False
  storage_ok = True
  for batch in message_batches(services):
    for event in batch:
      if telemetry.feed(event) != 'carState':
        continue
      t = telemetry.messages['carState'][0]
      if t - last_refresh >= 5:
        try:
          next_mode = store.mode()
          if not targets_loaded or not storage_ok or next_mode != mode:
            matcher = StopMatcher(store.list(cp.carFingerprint))
            targets_loaded = True
          mode = next_mode
          with closing(store.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('drive',?)", (json.dumps({"id": drive, "seen": time.time()}),))  # noqa: TID251
          storage_ok = True
        except (sqlite3.Error, OSError, ValueError):
          cloudlog.exception('learned_stop_storage_unavailable')
          matcher, storage_ok = StopMatcher([]), False
        last_refresh = t
        # Route identity is ignition-level metadata, not a 100Hz signal.
        current_route = params.get('CurrentRoute') or b''
        current_route = current_route.decode() if isinstance(current_route, bytes) else current_route
        if current_route != route:
          route = current_route
          telemetry.frames.clear()
          observer = Observer(drive)
      encoder = telemetry.get('roadEncodeIdx', t, 2)
      source = f'{route}--{encoder.segmentNum}' if route and encoder is not None else route
      result = telemetry.sample(source)
      target = None
      available = False
      if result and mode != "off":
        sample, heading, complete = result
        available = sample.located() and sample.accuracy is not None and complete and storage_ok
        observation = observer.update(sample)
        if observation:
          telemetry.evidence(observation, route)
          try:
            stored = store.add(cp.carFingerprint, observation)
            cloudlog.event("learned_stop_observation", **stored, observation=observation)
            matcher = StopMatcher(store.list(cp.carFingerprint))
          except (sqlite3.Error, OSError, ValueError):
            cloudlog.exception('learned_stop_observation_not_saved')
            available = storage_ok = False
        # Matching was previously repeated at 100Hz, then discarded between 10Hz publications.
        if t - last_publish >= 0.1 and available:
          target = matcher.find(sample, heading)
      elif mode == "off" and observer.history:
        observer = Observer(drive)
      if t - last_publish >= 0.1:
        message = messaging.new_message("learnedStopsBP")
        message.valid = bool(result)
        message.learnedStopsBP.data = json.dumps({"version": 1, "mode": mode, "available": available,
                                                 'error': '' if storage_ok else 'storage_unavailable',
                                                 "accuracy": result[0].accuracy if result else None,
                                                 "vehicle": cp.carFingerprint, "target": target}, allow_nan=False)
        pm.send("learnedStopsBP", message)
        last_publish = t


if __name__ == "__main__":
  main()
