"""Planner adapter: only message snapshots and bounded persistent hold state."""
from __future__ import annotations

import json
import math

from typing import Any, cast

from openpilot.common.params import Params
from .core import Sample, StopController
from .types import Latch, StopState, Target


class PlannerStops:
  def __init__(self, cp: Any, params: Params) -> None:
    self.params = params
    self.vehicle = cp.carFingerprint
    self.supported = self.vehicle == "FORD_F_150_MK14" and cp.openpilotLongitudinalControl
    self.controller = StopController()
    self.state: StopState = {"mode": "off", "state": "off", "apply": False, "hold": False}
    self.saved: str | None = None
    self.last_saved = 0.0
    try:
      value = json.loads(params.get("BPLearnedStopsLatch") or "{}")
      # Hold survives a planner restart only for this ignition/vehicle.
      route = params.get("CurrentRoute")
      route = route.decode() if isinstance(route, bytes) else route
      if isinstance(value, dict) and value.get("vehicle") == self.vehicle and value.get("route") == route and route:
        latch = value.get("latch")
        if (isinstance(latch, dict) and isinstance(latch.get("id"), str) and
            type(latch.get("distance")) in (int, float) and math.isfinite(latch["distance"]) and
            0 <= latch["distance"] <= 600 and type(latch.get("hold")) is bool and
            type(latch.get('setback', 0)) in (int, float) and 0 <= latch.get('setback', 0) <= 10):
          self.controller.latch = cast(Latch, latch)
        suppressed = value.get('suppressed', [])
        if isinstance(suppressed, list) and len(suppressed) <= 1000 and all(isinstance(s, str) for s in suppressed):
          self.controller.suppressed = set(suppressed)
    except (ValueError, TypeError):
      pass

  def update(self, sm: Any) -> StopState:
    # Stock maneuver fixtures provide a mapping without optional services.
    if not hasattr(sm, 'logMonoTime'):
      return self.state
    cs = sm["carState"]
    t = sm.logMonoTime["carState"] / 1e9
    data = {}
    if "learnedStopsBP" in sm.services and sm.valid["learnedStopsBP"] and -0.05 <= t - sm.logMonoTime["learnedStopsBP"] / 1e9 < 0.5:
      try:
        data = json.loads(sm["learnedStopsBP"].data)
      except (ValueError, TypeError):
        pass
    if not isinstance(data, dict) or data.get("vehicle") != self.vehicle:
      data = {}
    mode = data.get("mode", "control" if self.controller.latch else self.state['mode'])
    if mode not in ('off', 'observe', 'control'):
      mode = self.state['mode']
    target = data.get("target")
    if (not isinstance(target, dict) or not isinstance(target.get("id"), str) or
        type(target.get("ready")) is not bool or
        any(type(target.get(k)) not in (int, float) or not math.isfinite(target[k]) or target[k] < 0 for k in ("distance", "accuracy"))):
      target = None
    accuracy = data.get("accuracy")
    if accuracy is not None and (not isinstance(accuracy, (int, float)) or not math.isfinite(accuracy) or accuracy <= 0):
      accuracy = None
    sample = Sample(t=t, utc=0, speed=cs.vEgo, accuracy=accuracy, brake=cs.brakePressed, gas=cs.gasPressed)
    cancel = any(str(b.type) == "cancel" and b.pressed for b in cs.buttonEvents)
    self.state = self.controller.update(sample, mode if self.supported or mode == "off" else "observe", cast(Target | None, target),
                                        engaged=sm["carControl"].longActive, cancel=cancel)
    self.state.update({'confirmed': bool(target and target.get('confirmed')), 'visits': target.get('visits', 0) if target else 0})
    if mode != "off" and not data.get("available") and not self.controller.latch:
      self.state.update({'state': "paused", 'reason': data.get('error') or ('position_unavailable' if data else 'source_unavailable')})
    if not self.supported and mode != "off":
      self.state.update({'state': "unavailable", 'reason': "unsupported_vehicle"})
    saved_state = {'latch': self.controller.latch, 'suppressed': sorted(self.controller.suppressed)}
    encoded = json.dumps(saved_state, allow_nan=False)
    if encoded != self.saved and (t - self.last_saved >= 1 or self.controller.latch is None or self.state["hold"]):
      route = self.params.get("CurrentRoute")
      route = route.decode() if isinstance(route, bytes) else route
      self.params.put("BPLearnedStopsLatch", json.dumps({"vehicle": self.vehicle, "route": route, **saved_state}), block=False)
      self.saved, self.last_saved = encoded, t
    return self.state
