import pyray as rl

from bluepilot.ui.widgets.debug.autocal_bars import AutoCalBars
from openpilot.selfdrive.ui import UI_BORDER_SIZE
from openpilot.selfdrive.ui.bp.onroad.augmented_road_view_bp import (
  AUTO_CAL_BUTTON_GAP,
  auto_cal_bars_rect,
)
from openpilot.selfdrive.ui.onroad.driver_state import BTN_SIZE


def test_auto_cal_bars_align_below_steering_wheel_button():
  content_rect = rl.Rectangle(30, 30, 2100, 1020)

  bars_rect = auto_cal_bars_rect(content_rect)
  button_left = content_rect.x + content_rect.width - UI_BORDER_SIZE - BTN_SIZE

  assert bars_rect.x == button_left + (BTN_SIZE - AutoCalBars.WIDTH) / 2
  assert bars_rect.y == content_rect.y + UI_BORDER_SIZE + BTN_SIZE + AUTO_CAL_BUTTON_GAP
  assert bars_rect.width == AutoCalBars.WIDTH
  assert bars_rect.height == AutoCalBars.HEIGHT
