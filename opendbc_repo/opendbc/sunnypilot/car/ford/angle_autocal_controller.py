"""BluePilot: lifecycle controller for the Ford angle-mode auto-calibration.

AutoCalPipeline (angle_autocal.py) is pure math with no I/O. This controller owns
everything between that math and the car: arm/disarm from the toggle, evidence
persistence to FordAngleAutoCalState, nudge writes to the factor params, errors to
FordAngleAutoCalError, and the telemetry status string. FordLateralAngleExt calls
poll_params() at ~1 Hz, feed() per 20 Hz lateral frame, and idle() when inactive.

Nudges are written straight to the factor params (blocking); the strategy reads them
back through poll_params, so the live steering factors have a single owner and no
in-memory adopt path is needed.
"""
import json
import time
from collections import deque
from typing import Protocol

from opendbc.sunnypilot.car.ford.angle_autocal import AutoCalPipeline, Frame

SAVE_PERIOD_S = 30.0
EDIT_TOL = 0.005    # half the menu granularity (0.01): a factor moved further than this
# without the nudger writing it is a driver hand-edit


class ParamsLike(Protocol):
  """The Params calls used here; native Params is a dynamic C++ boundary."""
  def get_bool(self, key: str) -> bool: ...
  def get(self, key: str, return_default: bool = False) -> str | bytes | None: ...
  def put(self, key: str, value: str | float, block: bool = False) -> None: ...
  def put_bool(self, key: str, value: bool) -> None: ...


def _state_locked(state: str) -> bool:
  """True when the persisted state says the calibration is finished.
  Legacy pre-JSON states ("done low=... high=... verified") stay honored."""
  if state.startswith("done"):
    return True
  if state.startswith("{"):
    try:
      d = json.loads(state)
      return d.get("v") == 1 and d.get("phase") == "locked"
    except (ValueError, AttributeError):
      return False
  return False


def _restore(pipeline: AutoCalPipeline, state: str) -> None:
  """Load serialized evidence into a fresh pipeline; anything unparseable (legacy round
  strings, garbage, empty) simply starts a fresh collection."""
  if not state.startswith("{"):
    return
  try:
    d = json.loads(state)
    pipe = d.get("pipe")
    if isinstance(pipe, dict) and int(d.get("v", 0)) == 1:
      pipeline.from_dict(pipe)
  except (ValueError, KeyError, TypeError):
    pass


class AutoCalController:
  def __init__(self, dt: float) -> None:
    self.dt = dt
    self.enabled = False
    self.done = True            # conservative until params are read
    self.pipeline: AutoCalPipeline | None = None
    self.status = ""            # live ground-truth status, published in telemetry
    self._params: ParamsLike | None = None
    self._last_written: tuple[float, float] | None = None
    self._save_s = 0.0
    self._dirty = False
    self.requested_enabled = False
    self.lock_enabled = True
    self.settings_seen = False
    self._events: deque[dict[str, object]] = deque(maxlen=16)
    self._event_seq = 0
    self.events_json = ""
    self._last_settings: dict[str, object] | None = None
    self._last_phase: str | None = None
    self._log_frame = 0
    self._event_burst = 0

  def record_event(self, kind: str, **detail: object) -> None:
    """Bounded history; repeated identical errors retain their first occurrence and count."""
    events = list(self._events)
    repeated = kind == "error" and events and events[-1]["kind"] == kind and events[-1].get("message") == detail.get("message")
    if repeated:
      count = events[-1].get("count", 1)
      events[-1] = {**events[-1], "count": (count if isinstance(count, int) else 1) + 1,
                    "last_mono_ns": time.monotonic_ns()}
    else:
      events = (events + [{"seq": self._event_seq + 1, "mono_ns": time.monotonic_ns(), "kind": kind, **detail}])[-16:]
    try:
      encoded = json.dumps({"v": 1, "events": events}, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
      return  # Invalid diagnostics must not interrupt steering or poison subsequent history.
    self._event_seq += not repeated
    self._events = deque(events, maxlen=16)
    self.events_json = encoded
    self._event_burst = 10

  def events_for_log(self) -> str:
    # 100 Hz publisher, qlog decimation 10: a 10-frame burst includes a qlog sample.
    # Repeat once a second so a new segment/late logger can recover the history.
    if self._log_frame % 100 == 0:
      self._event_burst = 10
    self._log_frame += 1
    emit = self._event_burst > 0
    self._event_burst = max(0, self._event_burst - 1)
    return self.events_json if emit else ""

  def record_settings(self, **settings: object) -> None:
    if settings != self._last_settings:
      self.record_event("settings", **settings)
      self._last_settings = settings

  # -- ~1 Hz: toggle, restore, user edits, status ------------------------------------------
  def poll_params(self, params: ParamsLike, low_factor: float, high_factor: float, platform_gain_high: float) -> None:
    """Arm/disarm from the toggle, restore evidence on arm, detect user hand-edits of the
    factor params, refresh the status string. low/high are the currently applied values."""
    try:
      if params.get_bool("FordAngleAutoCalReset"):
        # Erase calibration memory: evidence, error channel, lock and factors all go
        # back to neutral so the car steers stock immediately and collection restarts.
        # Idempotent with the UI's own param clears; covers non-UI writers too.
        params.put_bool("FordAngleAutoCalReset", False)
        params.put("FordAngleAutoCalState", "")
        params.put("FordAngleAutoCalError", "")
        params.put("FordLowSpeedFactor_ang", 1.0)
        params.put("FordHighSpeedFactor_ang", 1.0)
        self.pipeline = None
        self.done = False
        self._last_written = (1.0, 1.0)
        self._dirty = False
        self._params = params
        self.status = "reset"
        self.record_event("reset", low=1.0, high=1.0)
        return
      enabled = bool(params.get_bool("FordAngleAutoCal"))
      # Lock behavior toggle (default ON): with the lock OFF the calibration never
      # freezes — and an EXISTING lock is treated as "resume from this evidence", not
      # as finished, so flipping the toggle un-locks without losing anything.
      lock_on = bool(params.get_bool("FordAngleAutoCalLock"))
      self.requested_enabled, self.lock_enabled = enabled, lock_on
      self.settings_seen = True
      state = params.get("FordAngleAutoCalState", return_default=True) or ""
      if isinstance(state, bytes):
        state = state.decode("utf-8", errors="replace")
      if self.pipeline is None:
        self.done = _state_locked(state) and lock_on
      else:
        self.pipeline.lock_enabled = lock_on
        if not lock_on and self.pipeline.locked:
          self.record_event("unlock")
          self.pipeline.locked = False
          self.pipeline.stable_s = 0.0
        self.done = self.pipeline.locked
      self.enabled = enabled and not self.done
      if self.enabled and self.pipeline is None:
        # Arm: build the pipeline, restore prior-drive evidence, baseline the nudger on
        # the currently applied factors.
        self.pipeline = AutoCalPipeline(platform_gain_high, dt=self.dt)
        _restore(self.pipeline, state)
        self.record_event("arm", restored_samples=self.pipeline.est.n, low=low_factor, high=high_factor)
        self.pipeline.lock_enabled = lock_on
        if not lock_on and self.pipeline.locked:
          self.pipeline.locked = False  # resuming a previously locked calibration
          self.pipeline.stable_s = 0.0
        self._last_written = (float(low_factor), float(high_factor))
      elif not self.enabled:
        self.pipeline = None
      else:
        # User hand-edit: a factor param differs from what the nudger last wrote. The
        # nudger's own writes are blocking (_apply_nudge), so by the time we read here
        # they always match _last_written — any mismatch is the driver. Adopt their value
        # (already live in the strategy) and soft-reset confidence; evidence is not wiped.
        lw = self._last_written
        moved = lw is not None and (abs(low_factor - lw[0]) > EDIT_TOL
                                    or abs(high_factor - lw[1]) > EDIT_TOL)
        if moved:
          assert lw is not None and self.pipeline is not None
          self.record_event("external_factor_edit", previous=list(lw), low=low_factor, high=high_factor)
          self.pipeline.user_edit()
          self._last_written = (float(low_factor), float(high_factor))
          self._dirty = True
      self._params = params
      # Live status for telemetry: published from actual controller state (ground truth),
      # never from a param re-read — a param/telemetry mismatch is exactly the failure
      # mode that made earlier on-device issues undiagnosable.
      if self.done:
        self.status = "locked"
      elif not self.enabled:
        self.status = "off"
      else:
        # Armed: compact JSON so live dashboards (phone /lateral cards) can render the
        # per-anchor story — evidence progress, measured response, proposed step, and
        # the adjust-then-verify judgment — from the same ground truth the nudger uses.
        assert self.pipeline is not None
        ui = self.pipeline.ui_state(low_factor, high_factor)
        ui["n"] = self.pipeline.est.n
        ui["apexes"] = self.pipeline.peaks.apexes_committed
        ui["rejections"] = dict(self.pipeline.quality.counters)
        self.status = json.dumps(ui, separators=(",", ":"))
      phase = "locked" if self.done else "armed" if self.enabled else "off"
      if phase != self._last_phase:
        self.record_event("phase", phase=phase)
        self._last_phase = phase
    except Exception as e:
      self.enabled = False
      self.status = f"tick error: {type(e).__name__}: {e}"[:200]
      self._error(self.status)

  # -- 20 Hz frames ------------------------------------------------------------------------
  def idle(self) -> None:
    """Frames where lateral is inactive (disengaged / human turn / stall blip)."""
    if self.pipeline is not None:
      self.pipeline.idle()

  def feed(self, frame: Frame, delay_estimated: bool) -> None:
    """One active lateral frame. Nudges are written to the factor params (the strategy
    reads them back — single reader); the lock -> disarm transition and save cadence
    happen here."""
    if not self.enabled or self.pipeline is None:
      return
    if not delay_estimated:
      # Until lagd reports 'estimated', kappa_meas (via liveParameters, same locationd
      # stack) is still converging — idle so staged samples don't straddle the warmup.
      self.idle()
      return
    committed = self.pipeline.update(frame)
    if committed:
      self._dirty = True
    applied = (frame.low_factor, frame.high_factor)
    rec = self.pipeline.recommend(frame.low_factor, frame.high_factor)
    if rec is not None and self._apply_nudge(rec):
      applied = rec
    if self.pipeline.locked:
      self.record_event("lock", low=applied[0], high=applied[1])
      self._save("locked", applied)
      self.done = True
      self.enabled = False
      self.pipeline = None
    else:
      self._save_s += self.dt
      if self._dirty and self._save_s >= SAVE_PERIOD_S:
        self._save("collecting", applied)

  # -- params I/O --------------------------------------------------------------------------
  def _apply_nudge(self, rec: tuple[float, float]) -> bool:
    """Write a nudged factor pair to the params, blocking so the write has landed before
    the next poll reads it (that read/write ordering is what keeps a nudge from looking
    like a user edit — no timing guess). Returns True on success. The factor params are
    typed FLOAT; a write error is parked in FordAngleAutoCalError rather than swallowed."""
    low_new, high_new = rec
    if self._params is None:
      return False
    try:
      self._params.put("FordLowSpeedFactor_ang", float(low_new), True)
      self._params.put("FordHighSpeedFactor_ang", float(high_new), True)
    except Exception as e:
      self._error(f"nudge write failed: {type(e).__name__}: {e}")
      return False
    self.record_event("nudge", previous=self._last_written, low=float(low_new), high=float(high_new))
    self._last_written = (float(low_new), float(high_new))
    self._save("collecting", rec)
    return True

  def _error(self, msg: str) -> None:
    """Park diagnostics in their OWN param, never FordAngleAutoCalState: an error written
    just before ignition-off must not be able to overwrite the serialized evidence."""
    self.record_event("error", message=msg[:300])
    try:
      if self._params is not None:
        self._params.put("FordAngleAutoCalError", f"{msg[:300]}")
    except Exception:
      pass

  def _save(self, phase: str, applied: tuple[float, float]) -> None:
    """Serialize the pipeline into FordAngleAutoCalState (JSON). Async put is fine:
    a lost final write costs at most SAVE_PERIOD_S of evidence."""
    if self._params is None or self.pipeline is None:
      return
    d = {
      "v": 1,
      "phase": phase,
      "pipe": self.pipeline.to_dict(),
      "applied": {"low": round(applied[0], 2), "high": round(applied[1], 2)},
    }
    sol = self.pipeline.est.solve()
    if sol is not None:
      low_t, high_t, st = sol
      d["target"] = {"low": round(low_t, 2), "high": round(high_t, 2)}
      d["weight"] = {"low": round(st["weight_low"], 1), "high": round(st["weight_high"], 1)}
      d["stderr"] = {"low": round(st["stderr_eff_low"], 3), "high": round(st["stderr_eff_high"], 3)}
      d["stable_s"] = round(self.pipeline.stable_s, 1)
    try:
      self._params.put("FordAngleAutoCalState", json.dumps(d, separators=(",", ":")))
    except Exception as e:
      self._error(f"state save failed: {type(e).__name__}: {e}")
      return
    self._save_s = 0.0
    self._dirty = False
