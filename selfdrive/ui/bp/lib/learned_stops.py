"""Offroad stop-memory controls and concise onroad status for both displays."""
from typing import Any, cast
from openpilot.system.ui.widgets import Widget
import json
import math
import time

import pyray as rl

from openpilot.bluepilot.learned_stops.store import Store
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import DialogResult


def offroad() -> bool:
  return not ui_state.started and not ui_state.engaged and not ui_state.params.get_bool('IsOnroad')


def open_review() -> None:
  if not offroad():
    return
  dialog: Widget
  if gui_app.big_ui():
    from openpilot.selfdrive.ui.bp.widgets.web_server_qr_dialog_tici import WebServerQRDialogTici
    dialog = WebServerQRDialogTici(path='/learned-stops')
  else:
    from openpilot.selfdrive.ui.bp.mici.widgets.web_server_qr_dialog import WebServerQRDialog
    dialog = WebServerQRDialog(gui_app.pop_widget, path='/learned-stops')
  gui_app.push_widget(dialog)


def choose_mode() -> None:
  if not offroad():
    return
  store = Store()
  dialog: Widget
  if gui_app.big_ui():
    from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
    def confirm(result: DialogResult) -> None:
      if result == DialogResult.CONFIRM and offroad():
        store.set_mode(options.selection.lower())
    options = MultiOptionDialog('Learned Stops: you control braking',
                               ['Off', 'Observe'], store.mode().capitalize(), callback=confirm)
    dialog = options
  else:
    from openpilot.selfdrive.ui.bp.mici.widgets.button_bp import BigButtonBP
    from openpilot.system.ui.widgets.scroller import NavScroller
    dialog = NavScroller()
    dialog.set_back_callback(gui_app.pop_widget)
    for mode, caption in [('off', 'retain saved stops'), ('observe', 'you control braking')]:
      button = BigButtonBP(mode, caption)
      def select(mode: str = mode) -> None:
        if offroad():
          store.set_mode(mode)
          gui_app.pop_widget()
      button.set_click_callback(select)
      dialog._scroller.add_widgets([button])
    unavailable = BigButtonBP('control unavailable', 'physical validation needed')
    unavailable.set_enabled(False)
    dialog._scroller.add_widgets([unavailable])
  gui_app.push_widget(dialog)


def status_text(data: Any, compact: bool = False) -> str:
  if not isinstance(data, dict) or data.get('mode', 'off') == 'off':
    return ''
  state = data.get('state')
  if data.get('takeover'):
    return 'TAKE OVER: STOP'
  if state == 'holding':
    return 'Check traffic; accelerator to depart'
  if data.get('mode') == 'observe':
    prefix = 'OBSERVE: brake manually' if compact else 'Observe: you control braking'
    distance = data.get('distance')
    if state == 'approach' and type(distance) in (int, float) and math.isfinite(cast(float, distance)):
      kind = 'stop' if data.get('confirmed') else 'unverified stop'
      return f'{prefix} / {kind} {max(0, round(cast(float, distance)))} m'
    return prefix + (' / paused' if state in ('paused', 'unavailable') else ' / learning')
  return 'Stop assistance: ' + str(state)


class StopIndicator:
  def __init__(self) -> None:
    self.last_frame = -1
    self.text = ''

  def render(self, rect: rl.Rectangle, compact: bool = False) -> None:
    sm = ui_state.sm
    if 'longitudinalPlanSP' not in sm.services:
      return
    if (not sm.valid['longitudinalPlanSP'] or
        time.monotonic() - sm.logMonoTime['longitudinalPlanSP'] / 1e9 > 1):
      return
    # Critical alerts retain the screen; learning never competes with a takeover.
    if str(sm['selfdriveState'].alertStatus) == 'critical':
      return
    frame = sm.logMonoTime['longitudinalPlanSP']
    if frame != self.last_frame:
      self.last_frame = frame
      try:
        self.text = status_text(json.loads(sm['longitudinalPlanSP'].learnedStops or '{}'), compact)
      except (ValueError, TypeError):
        self.text = ''
    if not self.text:
      return
    font = gui_app.font(FontWeight.MEDIUM)
    size = 14 if compact else 30
    width = measure_text_cached(font, self.text, size).x
    while width > rect.width - 24 and size > 10:
      size -= 1
      width = measure_text_cached(font, self.text, size).x
    x, y = rect.x + (rect.width - width) / 2, rect.y + (78 if compact else 330)
    rl.draw_rectangle_rounded(rl.Rectangle(x - 8, y - 6, width + 16, size + 12), .3, 8, rl.Color(0, 0, 0, 180))
    rl.draw_text_ex(font, self.text, rl.Vector2(x, y), size, 0, rl.WHITE)
