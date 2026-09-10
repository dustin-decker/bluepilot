"""Reopening a collapsed menu must not stack previously expanded rows."""
import pytest
from typing import Any, cast

from openpilot.selfdrive.ui.bp.widgets.section_header import CollapsibleSectionHeader
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.list_view import ListItem
from openpilot.system.ui.sunnypilot.widgets.list_view import ListItemSP
from openpilot.system.ui.widgets.scroller_tici import Scroller


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('inline', [None, True, False])
def test_reopen_menu_after_expanding_description(monkeypatch: Any, nested: bool, inline: bool | None) -> None:
  monkeypatch.setattr(gui_app, 'font', lambda _: None)
  kwargs = {} if inline is None else {'inline': inline}
  cls = ListItem if inline is None else ListItemSP
  item = cast(Any, cls)('Low-Speed Angle Factor', description='Steering factor explanation', **kwargs)
  base_height = item.rect.height
  monkeypatch.setattr(item._html_renderer, 'get_total_height', lambda _: 80)
  header = CollapsibleSectionHeader('Angle Tuning')
  header.set_items([item])
  items = [header, item]
  if nested:
    parent = CollapsibleSectionHeader('Lateral Tuning')
    parent.set_items([header])
    parent.set_nested_headers([header])
    items.insert(0, parent)
  scroller = Scroller(items)
  for _ in range(3):
    scroller.show_event()
    if nested:
      parent._toggle()
    header._toggle()
    assert item.is_visible and item.rect.height == base_height
    item._set_description_visible(True)
    assert item.rect.height > base_height
    scroller.hide_event()
