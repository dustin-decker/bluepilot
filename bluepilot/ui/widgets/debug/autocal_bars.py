"""Auto-calibration band gauges for the on-device lateral debug graph.

Two phone-battery-style vertical gauges — BLUE = low anchor, RED = high anchor —
in the strip left of the plot. Each fills bottom-up as
that band charges toward calibrated: partway while collecting evidence, up while a step
is being checked, and full once the band is calibrated. A full band stays full; when the
whole calibration locks the gauges become padlocks. Hidden while auto-calibration is off.

Status is parsed only when it changes (~1 Hz), off a conflated socket, so the widget is a
handful of rectangles per frame — cheap on the UI process.
"""
import json

import pyray as rl

from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.lib.application import gui_app, FontWeight
from opendbc.car.common.conversions import Conversions as CV
from opendbc.sunnypilot.car.ford.values_ext import V_LOW, V_HIGH

try:
  import cereal.messaging as _messaging
  _STATUS_SOCK = _messaging.sub_sock("controllerStateBP", conflate=True, timeout=0)
except Exception:  # PC/dev hosts without cereal sockets: gauges stay hidden
  _messaging = None
  _STATUS_SOCK = None

_BLUE = (77, 163, 255)
_RED = (230, 88, 88)
_BATT_W, _BATT_H = 22, 44
_NUB_W, _NUB_H = 10, 4
_LABEL_GAP = 4
_LABEL_FONT = 12
_ROW_PITCH = _NUB_H + _BATT_H + _LABEL_GAP + _LABEL_FONT + 10

# Fill is a single monotonic "progress toward calibrated" so the battery never fills then
# un-fills as the anchor moves between phases. Evidence alone tops out below full; only a
# calibrated band reads full (and latches there — see _fill's hysteresis).
_COLLECT_TOP = 0.7   # evidence progress maps into [0, _COLLECT_TOP]
_VERIFY_TOP = 0.9    # a step being checked maps into [_COLLECT_TOP, _VERIFY_TOP]


def poll_status() -> str | None:
  """Newest bmsAngleAutoCalState string, or None when nothing new / no socket."""
  if _STATUS_SOCK is None:
    return None
  msg = _messaging.recv_one_or_none(_STATUS_SOCK)
  if msg is None:
    return None
  return str(msg.controllerStateBP.bmsAngleAutoCalState)


def band_fill(band_st: dict, was_full: bool) -> tuple[float, bool]:
  """Monotonic 'progress toward calibrated' fill (0..1) for one band, plus the new
  latched-full state. Pure, so it is unit-tested directly. 'good' latches full and stays
  full through borderline good/propose flicker; a fresh 'verify' step or a 'collect' reset
  drops it. Evidence alone (collect) tops out below full — the fill-then-un-fill bug was
  showing three different metrics per phase; this is one."""
  ph = band_st.get("ph", "collect")
  full = was_full
  if ph == "good":
    full = True
  elif ph in ("verify", "collect"):
    full = False
  if full:
    return 1.0, full
  if ph == "verify":
    need = max(float(band_st.get("vneed", 6)), 1e-6)
    return _COLLECT_TOP + (_VERIFY_TOP - _COLLECT_TOP) * min(1.0, float(band_st.get("vw", 0.0)) / need), full
  if ph == "propose":
    return _COLLECT_TOP, full
  need = max(float(band_st.get("need", 10)), 1e-6)  # collect
  return _COLLECT_TOP * min(1.0, float(band_st.get("w", 0.0)) / need), full


class AutoCalBars(Widget):
  """Hidden whenever auto-calibration is off / status is not renderable."""

  WIDTH = _BATT_W + 8
  HEIGHT = 2 * _ROW_PITCH

  def __init__(self, scale: float = 1.0):
    super().__init__()
    if scale <= 0:
      raise ValueError("scale must be positive")
    self._scale = float(scale)
    self._raw = None
    self._st = None                        # dict (armed JSON) | "locked" | None
    self._full = {"low": False, "high": False}  # latched-full state per band (hysteresis)

  @property
  def render_width(self) -> int:
    return round(self.WIDTH * self._scale)

  @property
  def render_height(self) -> int:
    return round(self.HEIGHT * self._scale)

  def update_status(self, status: str | None):
    if status is None or status == self._raw:
      return
    self._raw = status
    if status.startswith("{"):
      try:
        d = json.loads(status)
        self._st = d if ("low" in d and "high" in d) else None
      except ValueError:
        self._st = None
    elif status == "locked":
      self._st = "locked"
    else:
      self._st = None

  @property
  def active(self) -> bool:
    return self._st is not None

  @property
  def in_progress(self) -> bool:
    """True only while auto-calibration is collecting or verifying."""
    return isinstance(self._st, dict)

  @property
  def locked(self) -> bool:
    return self._st == "locked"

  def _fill(self, band: str) -> float:
    fill, self._full[band] = band_fill(self._st.get(band, {}), self._full[band])
    return fill

  def _draw_battery(self, x: int, y: int, fill: float, rgb):
    batt_w = round(_BATT_W * self._scale)
    batt_h = round(_BATT_H * self._scale)
    nub_w = round(_NUB_W * self._scale)
    nub_h = round(_NUB_H * self._scale)
    pad = max(1, round(2 * self._scale))
    col = rl.Color(rgb[0], rgb[1], rgb[2], 235)
    body_y = int(y + nub_h)
    rl.draw_rectangle(x + (batt_w - nub_w) // 2, int(y), nub_w, nub_h, rl.Color(150, 155, 165, 200))
    body = rl.Rectangle(x, body_y, batt_w, batt_h)
    rl.draw_rectangle_rounded(body, 0.25, 6, rl.Color(38, 42, 52, 200))
    fh = int((batt_h - 2 * pad) * fill)
    if fh > 0:
      rl.draw_rectangle(x + pad, body_y + batt_h - pad - fh, batt_w - 2 * pad, fh, col)
    rl.draw_rectangle_rounded_lines_ex(body, 0.25, 6, 1.5 * self._scale, rl.Color(150, 155, 165, 200))

  def _draw_lock(self, x: int, y: int, rgb):
    batt_w = round(_BATT_W * self._scale)
    batt_h = round(_BATT_H * self._scale)
    nub_h = round(_NUB_H * self._scale)
    col = rl.Color(rgb[0], rgb[1], rgb[2], 235)
    body_w, body_h = batt_w, round(26 * self._scale)
    body_y = int(y + nub_h + batt_h - body_h)
    # shackle: a half-ring sitting on the body
    cx = x + batt_w / 2
    rl.draw_ring(rl.Vector2(cx, body_y), 6.0 * self._scale, 9.0 * self._scale, 180.0, 360.0, 16, col)
    body = rl.Rectangle(x, body_y, body_w, body_h)
    rl.draw_rectangle_rounded(body, 0.3, 6, col)
    rl.draw_circle(int(cx), body_y + body_h // 2, 2.5 * self._scale, rl.Color(20, 22, 28, 255))  # keyhole

  def _render(self, rect: rl.Rectangle):
    if self._st is None:
      return
    font = gui_app.font(FontWeight.NORMAL)
    y = rect.y
    x = int(rect.x)
    batt_w = round(_BATT_W * self._scale)
    batt_h = round(_BATT_H * self._scale)
    nub_h = round(_NUB_H * self._scale)
    label_gap = round(_LABEL_GAP * self._scale)
    label_font = round(_LABEL_FONT * self._scale)
    row_pitch = round(_ROW_PITCH * self._scale)
    for band, rgb, label in (("low", _BLUE, f"<{V_LOW * CV.MS_TO_MPH:.0f}"), ("high", _RED, f">{V_HIGH * CV.MS_TO_MPH:.0f}")):
      if self.locked:
        self._draw_lock(x, int(y), rgb)
      else:
        try:
          self._draw_battery(x, int(y), self._fill(band), rgb)
        except (TypeError, ValueError, KeyError):
          pass
      rl.draw_text_ex(font, label,
                      rl.Vector2(x + (batt_w - label_font * len(label) * 0.55) / 2,
                                 y + nub_h + batt_h + label_gap),
                      label_font, 0, rl.Color(160, 165, 175, 210))
      y += row_pitch
