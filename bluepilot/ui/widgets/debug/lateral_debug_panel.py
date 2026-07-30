"""
BluePilot Lateral Debug Panel
Displays steering angle and curvature time-series graphs.
Port of Qt LateralDebugPanel + LateralGraphWidget.
"""

from __future__ import annotations

import time
import pyray as rl
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.ui_state import ui_state
from bluepilot.ui.widgets.debug.debug_colors import DebugColors
from bluepilot.ui.widgets.debug.debug_graph import TimeSeriesGraph, GraphConfig, GraphSeries
from bluepilot.ui.widgets.debug.angle_factor_adjuster import AngleFactorAdjuster
from bluepilot.ui.widgets.debug.autocal_bars import AutoCalBars, poll_status

# Match Qt update rate: 20Hz = 50ms between data pushes
DATA_PUSH_INTERVAL = 0.05
CAL_POLL_INTERVAL = 1.0
ADJUSTER_WIDTH = 170
ADJUSTER_MARGIN_TOP = 15
ADJUSTER_MARGIN_RIGHT = 15
CAL_BARS_MARGIN = 12
CAL_BARS_GUTTER = AutoCalBars.WIDTH + 2 * CAL_BARS_MARGIN
CAL_BARS_MARGIN_TOP = 70


def calibration_layout(rect: rl.Rectangle, active: bool) -> tuple[rl.Rectangle, rl.Rectangle | None]:
  """Reserve a left gutter for C3X gauges without moving the graph's right edge."""
  if not active:
    return rect, None

  graph_rect = rl.Rectangle(
    rect.x + CAL_BARS_GUTTER,
    rect.y,
    max(0.0, rect.width - CAL_BARS_GUTTER),
    rect.height,
  )
  bars_rect = rl.Rectangle(
    rect.x + CAL_BARS_MARGIN,
    rect.y + CAL_BARS_MARGIN_TOP,
    AutoCalBars.WIDTH,
    AutoCalBars.HEIGHT,
  )
  return graph_rect, bars_rect


class LateralDebugPanel(Widget):
  """Lateral control debug panel with steering angle/curvature graph."""

  MAX_DATA_POINTS = 100

  def __init__(self):
    super().__init__()
    self._graph = TimeSeriesGraph(
      config=GraphConfig(
        title="Lateral Control",
        y_unit="\u00b0",
        max_data_points=self.MAX_DATA_POINTS,
        min_scale=5.0,
      ),
      series=[
        GraphSeries("Angle Desired", DebugColors.DESIRED_GREEN, fill_alpha=25),
        GraphSeries("Angle Actual", DebugColors.ACTUAL_YELLOW, fill_alpha=25, beaded=True),
        GraphSeries("Desired Curv", DebugColors.DESIRED_CURV_CYAN),
        GraphSeries("Actual Curv", DebugColors.ACTUAL_CURV_MAGENTA),
      ]
    )
    self._steer_delay = 0.0
    self._last_push_time = 0.0
    self._adjuster = self._child(AngleFactorAdjuster())
    self._cal_bars = AutoCalBars()
    self._last_cal_poll = 0.0

  def _update_state(self):
    sm = ui_state.sm
    if sm is None:
      return

    # Throttle data pushes to ~20Hz to match Qt version
    now = time.monotonic()
    if now - self._last_push_time < DATA_PUSH_INTERVAL:
      return

    actual_angle = 0.0
    desired_angle = 0.0
    actual_curv = 0.0
    desired_curv = 0.0

    try:
      if sm.valid.get('carState', False):
        actual_angle = sm['carState'].steeringAngleDeg

      if sm.valid.get('carControl', False):
        desired_angle = sm['carControl'].actuators.steeringAngleDeg

      if sm.valid.get('controlsState', False):
        cs = sm['controlsState']
        actual_curv = cs.curvature
        desired_curv = cs.desiredCurvature

      if sm.valid.get('carParams', False):
        self._steer_delay = sm['carParams'].steerActuatorDelay

      # Push data (curvature scaled by 100 so it's visible on the angle scale)
      self._graph.push_data([desired_angle, actual_angle,
                             desired_curv * 100.0, actual_curv * 100.0])
      self._last_push_time = now

      self._graph.set_extra_legend([
        ("Steer Delay", f"{self._steer_delay:.3f}s"),
      ])
    except (KeyError, AttributeError, ValueError):
      pass

    if now - self._last_cal_poll >= CAL_POLL_INTERVAL:
      self._last_cal_poll = now
      self._cal_bars.update_status(poll_status())

  def _render(self, rect: rl.Rectangle):
    graph_rect, bars_rect = calibration_layout(rect, self._cal_bars.active)
    self._graph.render(graph_rect)

    if bars_rect is not None:
      self._cal_bars.render(bars_rect)

    # Angle-factor adjuster overlaps the graph's right edge (angle mode only)
    if self._adjuster.is_visible:
      adjuster_rect = rl.Rectangle(rect.x + rect.width - ADJUSTER_WIDTH - ADJUSTER_MARGIN_RIGHT,
                                   rect.y + ADJUSTER_MARGIN_TOP,
                                   ADJUSTER_WIDTH, rect.height - ADJUSTER_MARGIN_TOP)
      self._adjuster.render(adjuster_rect)
