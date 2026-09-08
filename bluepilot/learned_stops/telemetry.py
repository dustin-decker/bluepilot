"""One telemetry decoder for live messages and chronological rlog replay."""
from __future__ import annotations

from typing import Any, NamedTuple
import math
from collections import deque

from .core import Sample


class Location(NamedTuple):
  lat: float | None = None
  lon: float | None = None
  accuracy: float | None = None
  gps_heading: float | None = None
  yaw_heading: float | None = None
  utc: float = 0


def decode_location(gps: Any, llk: Any) -> Location:
  """Decode unchanged native localization messages once, not per carState tick."""
  lat = lon = accuracy = gps_heading = yaw_heading = None
  utc = gps.unixTimestampMillis / 1000 if gps is not None else 0
  if gps is not None and gps.hasFix and utc > 0:
    lat, lon = gps.latitude, gps.longitude
    if 0 < gps.bearingAccuracyDeg < 15:
      gps_heading = gps.bearingDeg
    if llk is not None and llk.gpsOK and llk.inputsOK and llk.sensorsOK and llk.positionECEF.valid and llk.positionGeodetic.valid:
      position = list(llk.positionGeodetic.value)
      errors = list(llk.positionECEF.std)
      if len(position) == 3 and len(errors) == 3 and all(math.isfinite(v) for v in position + errors) and all(v > 0 for v in errors):
        lat, lon = position[:2]
        accuracy = 2.448 * math.sqrt(sum(v * v for v in errors))
      if llk.calibratedOrientationNED.valid and len(llk.calibratedOrientationNED.value) == 3:
        yaw = llk.calibratedOrientationNED.value[2]
        if math.isfinite(yaw):
          yaw_heading = math.degrees(yaw) % 360
  return Location(lat, lon, accuracy, gps_heading, yaw_heading, utc)


class Telemetry:
  def __init__(self, gps_service: str = "gpsLocation") -> None:
    self.messages: dict[str, tuple[float, bool, Any]] = {}
    self.gps_service = gps_service
    self.frames: deque[dict[str, Any]] = deque()
    self._gps: Any = None
    self._llk: Any = None
    self._location = Location()
    self._radar: Any = None
    self._model: Any = None
    self._control: Any = None
    self._lead: float | None = None
    self._model_stop = False
    self._long_active = True

  def feed(self, event: Any) -> str:
    kind = event.which()
    payload = getattr(event, kind)
    self.messages[kind] = (event.logMonoTime / 1e9, event.valid, payload)
    if kind in ("roadEncodeIdx", "wideRoadEncodeIdx"):
      self.record_frame(kind, payload)
    return kind

  def record_frame(self, kind: str, payload: Any) -> None:
    if kind not in ("roadEncodeIdx", "wideRoadEncodeIdx"):
      return
    t = payload.timestampSof / 1e9
    self.frames.append({"t": t, "camera": "front" if kind == "roadEncodeIdx" else "wide",
                        "segment": payload.segmentNum, "index": payload.segmentId, "frame_id": payload.frameId})
    while self.frames and self.frames[0]["t"] < t - 45:
      self.frames.popleft()

  def evidence(self, observation: dict[str, Any], route: str) -> None:
    frames: list[dict[str, Any]] = []
    if not route:
      observation['frames'] = frames
      return
    for camera in ("front", "wide"):
      for offset in (-8, -2, 0):
        wanted = observation["t"] + offset
        candidates = [f for f in self.frames if f["camera"] == camera]
        nearest = min(candidates, key=lambda f: abs(f["t"] - wanted), default=None)
        if nearest and abs(nearest["t"] - wanted) <= 0.1:
          frames.append(dict(nearest, route=route, offset=offset, alignment_error=nearest["t"] - wanted))
    observation["frames"] = frames

  def get(self, name: str, t: float, age: float = 1) -> Any:
    item = self.messages.get(name)
    # Independent publishers may stamp the same live update a few ms after CS.
    # Replay is chronological and never feeds future events into this cache.
    return item[2] if item and item[1] and -0.05 <= t - item[0] <= age else None

  def sample(self, source: str = "") -> tuple[Sample, float | None, bool] | None:
    item = self.messages.get("carState")
    if not item or not item[1]:
      return None
    t, _, cs = item
    speed = cs.vEgo
    if not cs.canValid or not math.isfinite(speed):
      return None
    gps = self.get(self.gps_service, t, 2)
    llk = self.get("liveLocationKalman", t, .2)
    cc = self.get("carControl", t)
    radar = self.get("radarState", t)
    model = self.get("modelV2", t)
    if gps is not self._gps or llk is not self._llk:
      self._location = decode_location(gps, llk)
      self._gps, self._llk = gps, llk
    # Readers are immutable. Reuse decoded values until their message changes;
    # freshness is still evaluated above at every carState timestamp.
    if radar is not self._radar:
      lead = radar.leadOne if radar is not None else None
      self._lead = lead.dRel if lead is not None and lead.status else None
      self._radar = radar
    if model is not self._model:
      self._model_stop = bool(model is not None and model.action.shouldStop)
      self._model = model
    if cc is not self._control:
      self._long_active = bool(cc is None or cc.longActive)
      self._control = cc
    location = self._location
    heading = location.yaw_heading if location.yaw_heading is not None else location.gps_heading if speed > 3 else None
    accuracy = location.accuracy
    if accuracy is not None:
      # Age/speed change at carState rate even when the position message does not.
      # Preserve the conservative displacement bound and all freshness checks.
      age = abs(t - self.messages['liveLocationKalman'][0])
      accuracy += max(0, speed) * age + 2.5 * age**2
    sample = Sample(t=t, utc=location.utc, speed=max(0, speed), lat=location.lat, lon=location.lon, accuracy=accuracy,
                    gear=str(cs.gearShifter), brake=cs.brakePressed, gas=cs.gasPressed,
                    long_active=self._long_active,  # missing control data cannot prove a manual stop
                    lead=self._lead,
                    model_stop=self._model_stop, source=source,
                    complete=radar is not None and cc is not None)
    return sample, heading, radar is not None and cc is not None
