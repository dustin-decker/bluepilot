"""Separate, confirmed resets for steering learning; camera resets stay in Device."""
from openpilot.common.params import Params
from openpilot.system.ui.widgets import Widget
from cereal import log, messaging
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog, alert_dialog


def reset_camera_calibration(params: Params, restart: bool = False) -> None:
  params.remove('CalibrationParams')
  if restart:
    params.put_bool('OnroadCycleRequested', True, block=True)


def steering_learning_description(params: Params, key: str) -> str:
  desc = tr('Relearning takes time over future drives. Vehicle must be off to reset.')
  data = params.get(key)
  if data:
    try:
      event = messaging.log_from_bytes(data, log.Event)
      progress = event.liveDelay.calPerc if key == 'LiveDelay' else event.liveTorqueParameters.calPerc
      desc += '<br><br>' + tr('Learning is {}% complete.').format(round(progress))
    except Exception:
      cloudlog.exception('invalid ' + key)
  return desc


def prompt_steering_reset(key: str) -> None:
  title, short_title = {
    'LiveDelay': (tr('Reset Steering Delay Calibration'), tr('reset steering delay?')),
    'LiveTorqueParameters': (tr('Reset Learned Torque Parameters'), tr('reset learned torque?')),
  }[key]
  from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog, BigDialog

  def offroad() -> bool:
    return not ui_state.started and not ui_state.engaged and not ui_state.params.get_bool('IsOnroad')

  if not offroad():
    message = tr('Turn the vehicle off before resetting steering calibration.')
    gui_app.push_widget(alert_dialog(message) if gui_app.big_ui() else BigDialog('', message))
    return

  def reset(result: DialogResult = DialogResult.CONFIRM) -> None:
    # Recheck when confirming: a dialog may have remained open during ignition-on.
    if result == DialogResult.CONFIRM and offroad():
      ui_state.params.remove(key)

  dialog: Widget
  if gui_app.big_ui():
    message = title + '?\n\n' + tr('Relearning takes time over future drives.')
    dialog = ConfirmDialog(message, tr('Reset and Relearn'), callback=reset)
  else:
    message = short_title + '\n' + tr('relearning takes\ndriving time')
    dialog = BigConfirmationDialog(message, gui_app.texture('icons_mici/settings/device/lkas.png', 64, 34), reset)
    dialog._slider._label.set_font_size(32)
  gui_app.push_widget(dialog)
