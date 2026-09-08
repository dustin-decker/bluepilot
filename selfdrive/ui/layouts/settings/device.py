import os
import math
from typing import Any

from cereal import messaging, log
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.onroad.driver_camera_dialog import DriverCameraDialog
# BluePilot: keep every camera-reset entry point camera-only.
from openpilot.selfdrive.ui.bp.lib.calibration_reset import reset_camera_calibration
# End BluePilot
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.layouts.onboarding import TrainingGuide
from openpilot.selfdrive.ui.widgets.pairing_dialog import PairingDialog
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import multilang, tr, tr_noop
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog, alert_dialog
from openpilot.system.ui.widgets.html_render import HtmlModal
from openpilot.system.ui.widgets.list_view import text_item, button_item, dual_button_item
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
from openpilot.system.ui.widgets.scroller_tici import Scroller

if gui_app.sunnypilot_ui():
  from openpilot.system.ui.sunnypilot.widgets.list_view import button_item_sp as button_item

# Description constants
DESCRIPTIONS = {
  'pair_device': tr_noop("Pair your device with comma connect (connect.comma.ai) and claim your comma prime offer."),
  'driver_camera': tr_noop("Preview the driver facing camera to ensure that driver monitoring has good visibility. (vehicle must be off)"),
  'reset_calibration': tr_noop("bluepilot requires the device to be mounted within 4° left or right and within 5° up or 9° down."),
  'review_guide': tr_noop("Review the rules, features, and limitations of bluepilot"),
}


class DeviceLayout(Widget):
  def __init__(self):
    super().__init__()

    self._params = Params()
    self._select_language_dialog: MultiOptionDialog | None = None
    self._fcc_dialog: HtmlModal | None = None
    self._training_guide: TrainingGuide | None = None

    items = self._initialize_items()
    self._scroller = Scroller(items, line_separator=True, spacing=0)

    ui_state.add_offroad_transition_callback(self._offroad_transition)

  def _initialize_items(self) -> Any:
    self._pair_device_btn = button_item(lambda: tr("Pair Device"), lambda: tr("PAIR"), lambda: tr(DESCRIPTIONS['pair_device']),
                                        callback=lambda: gui_app.push_widget(PairingDialog()))
    self._pair_device_btn.set_visible(lambda: not ui_state.prime_state.is_paired())

    self._reset_calib_btn = button_item(lambda: tr("Reset Camera Calibration"), lambda: tr("RESET"), lambda: tr(DESCRIPTIONS['reset_calibration']),
                                        callback=self._reset_calibration_prompt)
    self._reset_calib_btn.set_description_opened_callback(self._update_calib_description)

    self._power_off_btn = dual_button_item(lambda: tr("Reboot"), lambda: tr("Power Off"),
                                           left_callback=self._reboot_prompt, right_callback=self._power_off_prompt)

    items = [
      text_item(lambda: tr("Dongle ID"), self._params.get("DongleId") or (lambda: tr("N/A"))),
      text_item(lambda: tr("Serial"), self._params.get("HardwareSerial") or (lambda: tr("N/A"))),
      self._pair_device_btn,
      button_item(lambda: tr("Driver Camera"), lambda: tr("PREVIEW"), lambda: tr(DESCRIPTIONS['driver_camera']),
                  callback=lambda: gui_app.push_widget(DriverCameraDialog()), enabled=ui_state.is_offroad),
      self._reset_calib_btn,
      button_item(lambda: tr("Review Training Guide"), lambda: tr("REVIEW"), lambda: tr(DESCRIPTIONS['review_guide']),
                  self._on_review_training_guide, enabled=ui_state.is_offroad),
      button_item(lambda: tr("Regulatory"), lambda: tr("VIEW"), callback=self._on_regulatory, enabled=ui_state.is_offroad),
      button_item(lambda: tr("Change Language"), lambda: tr("CHANGE"), callback=self._show_language_dialog),
      self._power_off_btn,
    ]
    return items

  def _offroad_transition(self):
    self._power_off_btn.action_item.right_button.set_visible(ui_state.is_offroad())

  def show_event(self):
    super().show_event()
    self._scroller.show_event()

  def _render(self, rect):
    self._scroller.render(rect)

  def _show_language_dialog(self):
    def handle_language_selection(result: DialogResult):
      if result == DialogResult.CONFIRM and self._select_language_dialog:
        selected_language = multilang.languages[self._select_language_dialog.selection]
        multilang.change_language(selected_language)
        self._update_calib_description()
      self._select_language_dialog = None

    self._select_language_dialog = MultiOptionDialog(tr("Select a language"), multilang.languages, multilang.codes[multilang.language],
                                                     option_font_weight=FontWeight.UNIFONT, callback=handle_language_selection)
    gui_app.push_widget(self._select_language_dialog)

  def _reset_calibration_prompt(self) -> None:
    if ui_state.engaged:
      gui_app.push_widget(alert_dialog(tr("Disengage to Reset Camera Calibration")))
      return

    def reset_calibration(result: DialogResult) -> None:
      # Check engaged again in case it changed while the dialog was open
      if ui_state.engaged or result != DialogResult.CONFIRM:
        return

      # BluePilot: camera reset preserves learned steering calibration.
      reset_camera_calibration(self._params, restart=True)
      # End BluePilot
      self._update_calib_description()

    dialog = ConfirmDialog(tr("Are you sure you want to reset camera calibration?"), tr("Reset"), callback=reset_calibration)
    gui_app.push_widget(dialog)

  def _update_calib_description(self) -> None:
    desc = tr(DESCRIPTIONS['reset_calibration'])

    calib_bytes = self._params.get("CalibrationParams")
    if calib_bytes:
      try:
        calib = messaging.log_from_bytes(calib_bytes, log.Event).liveCalibration

        if calib.calStatus != log.LiveCalibrationData.Status.uncalibrated:
          pitch = math.degrees(calib.rpyCalib[1])
          yaw = math.degrees(calib.rpyCalib[2])
          desc += tr(" Your device is pointed {:.1f}° {} and {:.1f}° {}.").format(abs(pitch), tr("down") if pitch > 0 else tr("up"),
                                                                                  abs(yaw), tr("left") if yaw > 0 else tr("right"))
      except Exception:
        cloudlog.exception("invalid CalibrationParams")

    desc += "<br><br>"
    desc += tr("bluepilot is continuously calibrating, resetting is rarely required. " +
               "Resetting camera calibration will restart bluepilot if the car is powered on.")

    self._reset_calib_btn.set_description(desc)

  def _reboot_prompt(self):
    if ui_state.engaged:
      gui_app.push_widget(alert_dialog(tr("Disengage to Reboot")))
      return

    def perform_reboot(result: DialogResult):
      if not ui_state.engaged and result == DialogResult.CONFIRM:
        self._params.put_bool("DoReboot", True)

    dialog = ConfirmDialog(tr("Are you sure you want to reboot?"), tr("Reboot"), callback=perform_reboot)
    gui_app.push_widget(dialog)

  def _power_off_prompt(self):
    if ui_state.engaged:
      gui_app.push_widget(alert_dialog(tr("Disengage to Power Off")))
      return

    def perform_power_off(result: DialogResult):
      if not ui_state.engaged and result == DialogResult.CONFIRM:
        self._params.put_bool("DoShutdown", True)

    dialog = ConfirmDialog(tr("Are you sure you want to power off?"), tr("Power Off"), callback=perform_power_off)
    gui_app.push_widget(dialog)

  def _on_regulatory(self):
    if not self._fcc_dialog:
      self._fcc_dialog = HtmlModal(os.path.join(BASEDIR, "selfdrive/assets/offroad/fcc.html"))
    gui_app.push_widget(self._fcc_dialog)

  def _on_review_training_guide(self):
    if not self._training_guide:
      self._training_guide = TrainingGuide()
    gui_app.push_widget(self._training_guide)
