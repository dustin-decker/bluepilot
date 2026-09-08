"""Shared live/replay stop observations. All distances are metres, angles degrees."""
from collections import deque
from collections.abc import Sequence
from typing import Any, cast

from dataclasses import dataclass
import math
import statistics

from .types import Latch, StopState, Target


MAX_SPEED = 70 * 0.44704
# A field-validation gate, deliberately not a user preference. Simulation alone
# cannot validate the physical stop reference or localization error on this car.
CONTROL_VALIDATED = False


def xy(lat: float, lon: float, origin: Sequence[float]) -> tuple[float, float]:
  return ((lon - origin[1]) * 111320 * math.cos(math.radians(origin[0])), (lat - origin[0]) * 111320)


def angle_difference(a: float, b: float) -> float:
  return abs((a - b + 180) % 360 - 180)


@dataclass
class Sample:
  t: float
  utc: float
  speed: float
  lat: float | None = None
  lon: float | None = None
  accuracy: float | None = None  # conservative estimated horizontal 95% error
  gear: str = "drive"
  brake: bool = False
  gas: bool = False
  long_active: bool = False
  lead: float | None = None
  model_stop: bool = False
  source: str = ""
  complete: bool = True

  def located(self) -> bool:
    return (self.lat is not None and self.lon is not None and math.isfinite(self.lat) and math.isfinite(self.lon)
            and -90 <= self.lat <= 90 and -180 <= self.lon <= 180)


class Observer:
  """One observation per stationary spell; callers supply a stable drive ID.

  Semantic confirmation is deliberately separate: stopping does not prove a sign.
  """
  def __init__(self, drive: str) -> None:
    self.drive = drive
    self.history: deque[Sample] = deque()
    self.stationary: list[Sample] = []
    self.emitted = False
    self.previous_t: float | None = None

  def update(self, sample: Sample) -> dict[str, Any] | None:
    if self.previous_t is not None and not 0 < sample.t - self.previous_t <= 1:
      self.history.clear()
      self.stationary.clear()
      self.emitted = False
    self.previous_t = sample.t
    self.history.append(sample)
    while self.history and self.history[0].t < sample.t - 40:
      self.history.popleft()
    if sample.speed > 0.25:
      self.stationary.clear()
      self.emitted = False
      return None
    if self.emitted:
      return None
    self.stationary.append(sample)
    if sample.t - self.stationary[0].t < 1:
      return None
    self.emitted = True
    before = [s for s in self.history if s.t < self.stationary[0].t]
    if not before or max(s.speed for s in before) < 3:
      return None
    recent = [s for s in self.history if s.t >= sample.t - 3]
    reasons = []
    if any(not s.complete for s in recent):
      reasons.append("telemetry_missing")
    if any(s.gear != "drive" for s in recent):
      reasons.append("maneuver")
    if any(s.lead is not None and s.lead < 25 for s in recent):
      reasons.append("queue")
    if any(s.long_active for s in self.stationary) or not any(s.brake for s in recent):
      reasons.append("not_manual")
    stationary = [s for s in self.stationary if s.located()]
    if len(stationary) != len(self.stationary):
      return None  # Cannot give an unlocated observation a map position.
    lat = statistics.median(cast(float, s.lat) for s in stationary)
    lon = statistics.median(cast(float, s.lon) for s in stationary)
    approach = [s for s in before if s.located() and s.speed > 3]
    far = next((s for s in reversed(approach) if math.hypot(*xy(cast(float, s.lat), cast(float, s.lon), (lat, lon))) >= 12), None)
    if far is None:
      reasons.append("direction_unknown")
      bearing = None
    else:
      dx, dy = xy(lat, lon, (cast(float, far.lat), cast(float, far.lon)))
      bearing = math.degrees(math.atan2(dx, dy)) % 360
    errors = [s.accuracy for s in stationary]
    accuracy = max(cast(list[float], errors)) if all(v is not None and math.isfinite(v) and v > 0 for v in errors) else None
    if accuracy is None:
      reasons.append("accuracy_unavailable")
    # Retain a bounded breadcrumb path for direction/road matching and review.
    path: list[list[float]] = []
    for s in before + [sample]:
      if s.located() and (not path or math.hypot(*xy(cast(float, s.lat), cast(float, s.lon), path[-1])) >= 3):
        path.append([cast(float, s.lat), cast(float, s.lon)])
    path.append([lat, lon])
    return {"drive": self.drive, "utc": self.stationary[0].utc, "lat": lat, "lon": lon,
            "bearing": bearing, "accuracy": accuracy, "reasons": reasons, "path": path,
            "manual": "not_manual" not in reasons, "model_stop": any(s.model_stop for s in self.stationary),
            "source": self.stationary[0].source, "t": self.stationary[0].t, "evidence": []}


def match_path(sample: Sample, path: Sequence[Sequence[float]], heading: float | None) -> float | None:
  """Distance along the recorded approach, rejecting cross-track/wrong-way matches."""
  if not sample.located() or heading is None or len(path) < 2:
    return None
  origin = (cast(float, sample.lat), cast(float, sample.lon))
  longitude_scale = math.cos(math.radians(origin[0]))
  points = [((p[1] - origin[1]) * 111320 * longitude_scale, (p[0] - origin[0]) * 111320) for p in path]
  remaining = 0.0
  matches = []
  for a, b in reversed(list(zip(points, points[1:], strict=False))):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 0.1:
      continue
    fraction = -(a[0] * dx + a[1] * dy) / length**2
    if 0 <= fraction <= 1 and angle_difference(heading, math.degrees(math.atan2(dx, dy))) < 20:
      cross = math.hypot(a[0] + fraction * dx, a[1] + fraction * dy)
      if cross < 4:
        matches.append((cross, remaining + (1 - fraction) * length))
    remaining += length
  return min(matches)[1] if matches else None


class StopController:
  """Pure stopping constraint state machine; never commands departure or gas.

  Persistence of the returned latch belongs to the runtime adapter. `validated`
  is injectable for simulation, never sourced from the portal or vehicle Params.
  """
  def __init__(self, validated: bool = CONTROL_VALIDATED, latch: Latch | None = None) -> None:
    self.validated = validated
    self.latch = latch
    self.suppressed: set[str] = set()
    self.unmatched_since: float | None = None
    self.previous_t: float | None = None

  def update(self, s: Sample, mode: str, target: Target | None = None, engaged: bool = False, cancel: bool = False) -> StopState:
    dt = 0 if self.previous_t is None else s.t - self.previous_t
    self.previous_t = s.t
    if target is None and not self.latch:
      if self.unmatched_since is None:
        self.unmatched_since = s.t
      if s.t - self.unmatched_since >= 20:
        self.suppressed.clear()
    else:
      self.unmatched_since = None
    status: StopState = {"mode": mode, "state": "learning", "reason": "", "distance": None, "target": None,
              "apply": False, "hold": False, "takeover": False, "at_reference": False}
    if mode != "control" or not self.validated:
      self.latch = None
    if mode == "off":
      status["state"] = "off"
      return status
    if mode == 'control' and (s.gas or s.brake or cancel):
      if self.latch:
        self.suppressed.add(self.latch["id"])
      elif target:
        self.suppressed.add(target["id"])
      self.latch = None
      status["state"] = "driver"
      return status
    if self.latch:
      if not engaged:
        status.update({'state': "unavailable", 'reason': "not_engaged", 'takeover': True})
        return status
      if not 0 <= dt <= 1:
        status["takeover"] = True
      else:
        self.latch["distance"] = max(0, self.latch["distance"] - max(0, s.speed) * dt)
      # Never jump a latched stop farther away because GPS changed.
      if target and target["id"] == self.latch["id"]:
        measured = target['distance'] - self.latch.get('setback', 0)
        if abs(measured - self.latch['distance']) > 5 or s.accuracy is None or s.accuracy + target['accuracy'] > 2:
          status['takeover'] = True
        else:
          self.latch["distance"] = max(0, min(self.latch["distance"], measured))
      elif not self.latch.get('hold') or s.accuracy is None:
        status["takeover"] = True
      if s.speed < 0.25:
        self.latch["hold"] = True
      status.update({'state': 'holding' if self.latch.get('hold') else 'braking', 'target': self.latch['id'],
                     'distance': self.latch['distance'], 'apply': True, 'hold': self.latch.get('hold', False)})
      if status["hold"] and self.latch["distance"] >= 2:
        status["reason"] = "stopped_before_reference"
      status["at_reference"] = status["hold"] and self.latch["distance"] < 2
      return status
    if not target or target["id"] in self.suppressed:
      return status
    status.update({'target': target["id"], 'distance': target["distance"], 'state': "approach"})
    if mode != "control":
      return status
    reason = ("field_validation_required" if not self.validated else "not_engaged" if not engaged else
              "above_70_mph" if s.speed > MAX_SPEED else "position_unavailable" if s.accuracy is None else
              "position_uncertain" if s.accuracy + target["accuracy"] > 2 else
              "not_ready" if not target["ready"] else "")
    distance = target["distance"] - 2 - (s.accuracy or 0) - target["accuracy"]
    # Comfortable arrival envelope plus delay allowance. Never start a late stop.
    required = s.speed**2 / (2 * 1.2) + s.speed * 1.0
    if not reason and (distance <= 0 or distance < required):
      reason = "insufficient_distance"
    if reason:
      status.update({'state': "unavailable", 'reason': reason})
    elif distance <= required + 60:
      self.latch = {"id": target["id"], "distance": distance, "hold": False,
                    'setback': 2 + cast(float, s.accuracy) + target['accuracy']}
      status.update({'state': "braking", 'distance': distance, 'apply': True})
    return status
