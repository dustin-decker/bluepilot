import pyray as rl
import pytest

from bluepilot.ui.widgets.debug.autocal_bars import AutoCalBars
from openpilot.selfdrive.ui import UI_BORDER_SIZE
from openpilot.selfdrive.ui.bp.onroad.augmented_road_view_bp import (
  AUTO_CAL_BARS_HEIGHT,
  AUTO_CAL_BARS_WIDTH,
  AUTO_CAL_BUTTON_GAP,
  AUTO_CAL_LABEL_FONT_SIZE,
  AUTO_CAL_LABEL_GAP,
  auto_cal_bars_rect,
)
from openpilot.selfdrive.ui.onroad.driver_state import BTN_SIZE
from openpilot.selfdrive.ui.sunnypilot.onroad.developer_ui import DeveloperUiState


def test_auto_cal_bars_align_below_steering_wheel_button():
  content_rect = rl.Rectangle(30, 30, 2100, 1020)

  bars_rect = auto_cal_bars_rect(content_rect)
  button_left = content_rect.x + content_rect.width - UI_BORDER_SIZE - BTN_SIZE

  assert bars_rect.x == button_left + (BTN_SIZE - AUTO_CAL_BARS_WIDTH) / 2
  assert bars_rect.y == (
    content_rect.y + UI_BORDER_SIZE + BTN_SIZE + AUTO_CAL_BUTTON_GAP + AUTO_CAL_LABEL_FONT_SIZE + AUTO_CAL_LABEL_GAP
  )
  assert bars_rect.width == AUTO_CAL_BARS_WIDTH == AutoCalBars.WIDTH * 2
  assert bars_rect.height == AUTO_CAL_BARS_HEIGHT == AutoCalBars.HEIGHT * 2


@pytest.mark.parametrize('mode', [DeveloperUiState.RIGHT, DeveloperUiState.BOTH])
@pytest.mark.parametrize('width', [2100, 1800])
def test_autocal_clears_right_diagnostics_with_sidebar_open_or_closed(mode, width):
  content = rl.Rectangle(30, 30, width, 1020)
  bars = auto_cal_bars_rect(content, mode)
  panel_left = content.x + content.width - 184 - 40
  # Include half the label width, conservatively bounded by nine 30px characters.
  assert bars.x + bars.width / 2 + 9 * AUTO_CAL_LABEL_FONT_SIZE / 2 < panel_left
  assert bars.x >= content.x
