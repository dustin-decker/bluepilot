#!/usr/bin/env python3
"""Host-only deterministic screenshots of production model and autocal widgets.

These are component snapshots, not camera replay or whole-application screenshots.
Run each variant in a fresh process because BIG selects fonts at import time.
"""
import argparse
import json
import os
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

LONG_MODEL = 'GWM V8 — experimental driving model with a deliberately very long display name September 2026'


def false_param(_name: str) -> bool:
  return False


def render(variant: str, output: Path) -> None:
  if Path('/TICI').exists() or Path('/AGNOS').exists():
    raise RuntimeError('Screenshot fixtures are host-only; never start test graphics on a driving device.')
  # Only the hidden host window is scaled; snapshots render to full-resolution textures.
  os.environ.update(BIG='1' if variant == 'tici' else '0', SCALE='0.25', OFFSCREEN='1', ZMQ='1')
  import pyray as rl
  from openpilot.common.prefix import OpenpilotPrefix
  with OpenpilotPrefix():
    from openpilot.system.ui.lib.application import gui_app, FONT_DIR, FontWeight
    # Do not silently generate baselines with raylib's fallback font or LFS pointers.
    for font in FontWeight:
      path = Path(str(FONT_DIR)) / font.value
      if not path.exists() or not path.read_bytes().startswith(b'info '):
        raise RuntimeError(f'Missing generated font: {path}. Build UI/font assets first.')
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    gui_app.init_window('BluePilot component screenshots', fps=60)
    # The application's scissor wrapper targets its scaled window, not our full-size texture.
    rl.begin_scissor_mode = rl._orig_begin_scissor_mode
    gui_app._scale = 1.0  # Load full-resolution assets for the full-size fixture texture.
    rl.get_time = lambda: 1.0  # Freeze native shimmer animation for repeatable screenshots.
    try:
      output.mkdir(parents=True, exist_ok=True)
      width, height = (2160, 1080) if variant == 'tici' else (536, 240)
      texture = rl.load_render_texture(width, height)
      try:
        def snapshot(name: str, draw: Callable[[], object]) -> None:
          rl.begin_texture_mode(texture)
          rl.clear_background(rl.BLACK)
          draw()
          rl.end_texture_mode()
          image = rl.load_image_from_texture(texture.texture)
          try:
            rl.image_flip_vertical(image)
            if not rl.export_image(image, str(output / f'{variant}-{name}.png')):
              raise RuntimeError(f'Could not export {name}')
          finally:
            rl.unload_image(image)

        from openpilot.selfdrive.ui.bp.lib import calibration_reset
        cast(Any, calibration_reset).ui_state = SimpleNamespace(started=False, engaged=False, params=SimpleNamespace(get_bool=false_param))
        dialogs: list[Any] = []
        gui: Any = gui_app
        push_widget = gui.push_widget
        try:
          gui.push_widget = dialogs.append
          for key, name in [('LiveDelay', 'reset-delay'), ('LiveTorqueParameters', 'reset-torque')]:
            calibration_reset.prompt_steering_reset(key)
            dialog = dialogs[-1]
            snapshot(name, partial(dialog.render, rl.Rectangle(0, 0, width, height)))
        finally:
          gui.push_widget = push_widget

        from openpilot.selfdrive.ui.bp.lib import learned_stops
        from cereal import log
        import time
        os.environ['BP_LEARNED_STOPS_ROOT'] = str(output / 'isolated-stops')
        cast(Any, learned_stops).ui_state = SimpleNamespace(started=False, engaged=False, params=SimpleNamespace(get_bool=false_param))
        try:
          gui.push_widget = dialogs.append
          learned_stops.choose_mode()
          snapshot('stops-mode', lambda: dialogs[-1].render(rl.Rectangle(0, 0, width, height)))
        finally:
          gui.push_widget = push_widget
        class StopMessages(dict[str, Any]):
          services = ['longitudinalPlanSP', 'selfdriveState']
          valid = dict.fromkeys(services, True)
          logMonoTime = {'longitudinalPlanSP': int(time.monotonic() * 1e9)}
        messages = StopMessages(longitudinalPlanSP=SimpleNamespace(learnedStops=''), selfdriveState=log.SelfdriveState.new_message())
        cast(Any, learned_stops.ui_state).sm = messages
        for name, distance in [('learning', None), ('approach', 175)]:
          messages['longitudinalPlanSP'].learnedStops = json.dumps({'mode': 'observe', 'state': name, 'distance': distance})
          indicator = learned_stops.StopIndicator()
          snapshot('stops-' + name, partial(indicator.render, rl.Rectangle(0, 0, width, height), compact=variant == 'mici'))

        if variant == 'mici':
          from openpilot.selfdrive.ui.sunnypilot.mici.layouts.models import CurrentModelInfo
          from openpilot.system.ui.widgets.label import ScrollState
          for scrolling in (False, True):
            widget = CurrentModelInfo()
            widget.current_model_text.set_text(LONG_MODEL.lower())
            widget.info_text.set_text('1234 mb')
            # Fixed scroll phase instead of wall-clock sleeps.
            widget.current_model_text._scroll_state = ScrollState.SCROLLING
            widget.current_model_text._scroll_offset = -100 if scrolling else 0
            snapshot('model-scrolled' if scrolling else 'model-start', partial(widget.render, rl.Rectangle(12, 30, 360, 180)))
        else:
          from openpilot.system.ui.sunnypilot.widgets.list_view import ListItemSP
          from openpilot.system.ui.sunnypilot.lib.utils import NoElideButtonAction
          from openpilot.selfdrive.ui.bp.onroad import augmented_road_view_bp as road
          from openpilot.selfdrive.ui.sunnypilot.onroad import developer_ui as diagnostics
          from bluepilot.ui.widgets.debug.autocal_bars import AutoCalBars
          from cereal import log

          from openpilot.selfdrive.ui.bp.widgets.section_header import CollapsibleSectionHeader
          from openpilot.selfdrive.ui.bp.widgets.float_control_item import float_control_item
          from openpilot.system.ui.widgets.scroller_tici import Scroller
          rows = [float_control_item(title, 'Adjust the steering response.', param=key) for title, key in [
            ('Low-Speed Angle Factor', 'FordLowSpeedFactor_ang'),
            ('High-Speed Angle Factor', 'FordHighSpeedFactor_ang'),
            ('High-Speed Dampening', 'FordHighSpeedDampening_ang'),
          ]]
          angle_header = CollapsibleSectionHeader('Angle Tuning')
          angle_header.set_items(cast(list[Any], rows))
          lateral_header = CollapsibleSectionHeader('Lateral Tuning')
          lateral_header.set_items([angle_header])
          lateral_header.set_nested_headers([angle_header])
          menu = Scroller([lateral_header, angle_header, *rows], spacing=0, line_separator=True)
          for name in ('lateral-open', 'lateral-reopened'):
            menu.show_event()
            lateral_header._toggle()
            angle_header._toggle()
            snapshot(name, lambda: menu.render(rl.Rectangle(600, 60, 1500, 960)))
            for row in rows:
              row._set_description_visible(True)
            menu.hide_event()

          # Real cereal data, fed directly to widgets: no publishers or live routes.
          services = ('carState', 'carControl', 'controlsState', 'radarState', 'liveParameters', 'livePose',
                      'liveTorqueParameters', 'gpsLocation', 'gpsLocationExternal')
          class Messages(dict[str, Any]):
            valid = dict.fromkeys(services, False)

          sm = Messages({name: getattr(log.Event.new_message(**{name: {}}), name) for name in services})
          sm['controlsState'].lateralControlState.init('angleState')
          state = SimpleNamespace(sm=sm, is_metric=False, enforce_torque_control=False, custom_torque_params=False,
                                  torque_override_enabled=False, developer_ui=diagnostics.DeveloperUiState.OFF)
          # Only external state is replaced; all widget drawing and placement is production code.
          cast(Any, road).ui_state = cast(Any, diagnostics).ui_state = state
          diagnostics_renderer = diagnostics.DeveloperUiRenderer()
          bars = AutoCalBars(scale=road.AUTO_CAL_SCALE)
          bars.update_status(json.dumps({'low': {'ph': 'collect', 'w': 5, 'need': 10},
                                         'high': {'ph': 'verify', 'vw': 3, 'vneed': 6}}))
          host = SimpleNamespace(_auto_cal_bars=bars)
          for sidebar in (False, True):
            content = rl.Rectangle(330 if sidebar else 30, 30, 1800 if sidebar else 2100, 1020)
            row = ListItemSP(title='Current Model', action_item=NoElideButtonAction('SELECT'))
            row.set_parent_rect(content)
            assert row.action_item is not None
            cast(Any, row.action_item).set_value(LONG_MODEL)
            snapshot(f'model-sidebar-{int(sidebar)}', partial(row.render, rl.Rectangle(content.x, 100, content.width, 200)))
            for mode in diagnostics.DeveloperUiState:
              state.developer_ui = mode

              def draw(mode: Any = mode, content: Any = content) -> None:
                if mode in (diagnostics.DeveloperUiState.RIGHT, diagnostics.DeveloperUiState.BOTH):
                  diagnostics_renderer._draw_right_dev_ui(content)
                if mode in (diagnostics.DeveloperUiState.BOTTOM, diagnostics.DeveloperUiState.BOTH):
                  diagnostics_renderer._draw_bottom_dev_ui(content)
                cast(Any, road.AugmentedRoadViewBP)._render_auto_cal_bars(host, content)

              snapshot(f'autocal-sidebar-{int(sidebar)}-{mode.name.lower()}', draw)
      finally:
        rl.unload_render_texture(texture)
    finally:
      gui_app.close()


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--variant', choices=('tici', 'mici'), required=True)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  render(args.variant, args.output)


if __name__ == '__main__':
  main()
