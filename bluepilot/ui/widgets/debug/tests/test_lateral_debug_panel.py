import pyray as rl

from bluepilot.ui.widgets.debug.autocal_bars import AutoCalBars
from bluepilot.ui.widgets.debug.lateral_debug_panel import (
  CAL_BARS_GUTTER,
  CAL_BARS_MARGIN,
  CAL_BARS_MARGIN_TOP,
  calibration_layout,
)


def test_inactive_calibration_keeps_full_graph_rect():
  rect = rl.Rectangle(10, 20, 2000, 900)

  graph_rect, bars_rect = calibration_layout(rect, active=False)

  assert graph_rect is rect
  assert bars_rect is None


def test_active_calibration_reserves_c3x_gauge_gutter():
  rect = rl.Rectangle(10, 20, 2000, 900)

  graph_rect, bars_rect = calibration_layout(rect, active=True)

  assert bars_rect is not None
  assert bars_rect.x == rect.x + CAL_BARS_MARGIN
  assert bars_rect.y == rect.y + CAL_BARS_MARGIN_TOP
  assert bars_rect.width == AutoCalBars.WIDTH
  assert bars_rect.height == AutoCalBars.HEIGHT
  assert graph_rect.x == rect.x + CAL_BARS_GUTTER
  assert graph_rect.width == rect.width - CAL_BARS_GUTTER
  assert graph_rect.x + graph_rect.width == rect.x + rect.width
