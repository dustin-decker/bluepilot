"""BluePilot MICI: Longitudinal tuning panel — BP long bypass, downhill comp, Ford radar."""

from collections.abc import Callable

from openpilot.selfdrive.ui.bp.mici.widgets.button_bp import BigParamControlBP, BigButtonBP
from openpilot.selfdrive.ui.bp.lib.learned_stops import choose_mode, open_review
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.widgets.scroller import NavScroller


class LongitudinalLayoutMici(NavScroller):
  def __init__(self, back_callback: Callable[[], None] | None = None) -> None:
    super().__init__()
    if back_callback is not None:
      self.set_back_callback(back_callback)

    self.disable_BP_long = BigParamControlBP("bypass bp longitudinal control", "disable_BP_long_UI")
    self.disable_downhill_comp = BigParamControlBP("disable downhill compensation", "disable_downhill_comp_UI")
    self.disable_ford_radar = BigParamControlBP("disable ford radar (vision-only leads)", "disable_ford_radar_UI")
    stops = BigButtonBP('learned stops', 'observe: you control braking')
    stops.set_click_callback(choose_mode)
    review = BigButtonBP('review learned stops', 'route evidence in portal')
    review.set_click_callback(open_review)

    self._scroller.add_widgets([
      stops, review,
      self.disable_BP_long,
      self.disable_downhill_comp,
      self.disable_ford_radar,
    ])

    self._refresh_toggles = (
      ("disable_BP_long_UI", self.disable_BP_long),
      ("disable_downhill_comp_UI", self.disable_downhill_comp),
      ("disable_ford_radar_UI", self.disable_ford_radar),
    )

    ui_state.add_offroad_transition_callback(self._update_toggles)

  def show_event(self):
    super().show_event()
    self._update_toggles()

  def _update_toggles(self):
    ui_state.update_params()
    for key, item in self._refresh_toggles:
      item.set_checked(ui_state.params.get_bool(key))
