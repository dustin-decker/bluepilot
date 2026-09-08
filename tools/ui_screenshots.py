#!/usr/bin/env python3
"""Host-only deterministic screenshots of production model and autocal widgets.

These are component snapshots, not camera replay or whole-application screenshots.
Run each variant in a fresh process because BIG selects fonts at import time.
"""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

LONG_MODEL = 'GWM V8 — experimental driving model with a deliberately very long display name September 2026'


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
    try:
      output.mkdir(parents=True, exist_ok=True)
      width, height = (2160, 1080) if variant == 'tici' else (536, 240)
      texture = rl.load_render_texture(width, height)
      try:
        def snapshot(name, draw):
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
            snapshot('model-scrolled' if scrolling else 'model-start', lambda widget=widget: widget.render(rl.Rectangle(12, 30, 360, 180)))
        else:
          from openpilot.system.ui.sunnypilot.widgets.list_view import ListItemSP
          from openpilot.system.ui.sunnypilot.lib.utils import NoElideButtonAction
          from openpilot.selfdrive.ui.bp.onroad import augmented_road_view_bp as road
          from openpilot.selfdrive.ui.sunnypilot.onroad import developer_ui as diagnostics
          from bluepilot.ui.widgets.debug.autocal_bars import AutoCalBars
          from cereal import log

          # Real cereal data, fed directly to widgets: no publishers or live routes.
          services = ('carState', 'carControl', 'controlsState', 'radarState', 'liveParameters', 'livePose',
                      'liveTorqueParameters', 'gpsLocation', 'gpsLocationExternal')
          class Messages(dict):
            valid = dict.fromkeys(services, False)

          sm = Messages({name: getattr(log.Event.new_message(**{name: {}}), name) for name in services})
          sm['controlsState'].lateralControlState.init('angleState')
          state = SimpleNamespace(sm=sm, is_metric=False, enforce_torque_control=False, custom_torque_params=False,
                                  torque_override_enabled=False, developer_ui=diagnostics.DeveloperUiState.OFF)
          # Only external state is replaced; all widget drawing and placement is production code.
          road.ui_state = diagnostics.ui_state = state
          diagnostics_renderer = diagnostics.DeveloperUiRenderer()
          bars = AutoCalBars(scale=road.AUTO_CAL_SCALE)
          bars.update_status(json.dumps({'low': {'ph': 'collect', 'w': 5, 'need': 10},
                                         'high': {'ph': 'verify', 'vw': 3, 'vneed': 6}}))
          host = SimpleNamespace(_auto_cal_bars=bars)
          for sidebar in (False, True):
            content = rl.Rectangle(330 if sidebar else 30, 30, 1800 if sidebar else 2100, 1020)
            row = ListItemSP(title='Current Model', action_item=NoElideButtonAction('SELECT'))
            row.set_parent_rect(content)
            row.action_item.set_value(LONG_MODEL)
            snapshot(f'model-sidebar-{int(sidebar)}', lambda row=row, content=content: row.render(rl.Rectangle(content.x, 100, content.width, 200)))
            for mode in diagnostics.DeveloperUiState:
              state.developer_ui = mode

              def draw(mode=mode, content=content):
                if mode in (diagnostics.DeveloperUiState.RIGHT, diagnostics.DeveloperUiState.BOTH):
                  diagnostics_renderer._draw_right_dev_ui(content)
                if mode in (diagnostics.DeveloperUiState.BOTTOM, diagnostics.DeveloperUiState.BOTH):
                  diagnostics_renderer._draw_bottom_dev_ui(content)
                road.AugmentedRoadViewBP._render_auto_cal_bars(host, content)

              snapshot(f'autocal-sidebar-{int(sidebar)}-{mode.name.lower()}', draw)
      finally:
        rl.unload_render_texture(texture)
    finally:
      gui_app.close()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--variant', choices=('tici', 'mici'), required=True)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  render(args.variant, args.output)


if __name__ == '__main__':
  main()
