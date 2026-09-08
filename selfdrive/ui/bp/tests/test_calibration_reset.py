from types import SimpleNamespace
from typing import Any, cast

import pytest

from openpilot.selfdrive.ui.bp.lib import calibration_reset as reset
from openpilot.system.ui.widgets import DialogResult


class Params:
  def __init__(self) -> None:
    self.values: dict[str, Any] = dict.fromkeys(['CalibrationParams', 'LiveDelay', 'LiveTorqueParameters', 'LiveParameters',
                                'LiveParametersV2', 'FordAngleAutoCalState', 'FordLowSpeedFactor_ang'], b'learned')
    self.values['IsOnroad'] = False

  def get(self, key: str) -> Any:
    return self.values.get(key)

  def get_bool(self, key: str) -> bool:
    return bool(self.values.get(key))

  def remove(self, key: str) -> None:
    self.values.pop(key, None)

  def put_bool(self, key: str, value: bool, **kwargs: Any) -> None:
    self.values[key] = value


@pytest.mark.parametrize('restart', [False, True])
def test_camera_only_preserves_steering_learning(restart: bool) -> None:
  params = Params()
  expected = {k: v for k, v in params.values.items() if k != 'CalibrationParams'}
  if restart:
    expected['OnroadCycleRequested'] = True
  reset.reset_camera_calibration(cast(Any, params), restart=restart)
  assert params.values == expected


@pytest.mark.parametrize('big_ui', [False, True])
@pytest.mark.parametrize('key', ['LiveDelay', 'LiveTorqueParameters'])
def test_steering_reset_requires_confirmation_and_rechecks_offroad(monkeypatch: Any, big_ui: bool, key: str) -> None:
  from openpilot.selfdrive.ui.mici.widgets import dialog as mici

  params = Params()
  state = SimpleNamespace(params=params, started=False, engaged=False)
  shown: list[Any] = []

  def dialog(message: str, *args: Any, **kwargs: Any) -> Any:
    return SimpleNamespace(message=message, callback=kwargs.get('callback') or args[-1],
                           _slider=SimpleNamespace(_label=SimpleNamespace(set_font_size=lambda _: None)))

  monkeypatch.setattr(reset, 'ui_state', state)
  monkeypatch.setattr(reset, 'gui_app', SimpleNamespace(big_ui=lambda: big_ui, push_widget=shown.append, texture=lambda *args: None))
  monkeypatch.setattr(reset, 'ConfirmDialog', dialog)
  monkeypatch.setattr(reset, 'alert_dialog', lambda msg: SimpleNamespace(message=msg))
  monkeypatch.setattr(mici, 'BigConfirmationDialog', dialog)
  monkeypatch.setattr(mici, 'BigDialog', lambda _, msg: SimpleNamespace(message=msg))
  before = params.values.copy()
  reset.prompt_steering_reset(key)
  assert params.values == before and 'relearning' in shown[-1].message.lower()
  confirm = shown[-1].callback
  if big_ui:
    confirm(DialogResult.CANCEL)
    assert params.values == before
  state.started = True
  confirm()
  assert params.values == before
  state.started = False
  params.values['IsOnroad'] = True
  confirm()
  assert key in params.values
  params.values['IsOnroad'] = False
  confirm()
  assert params.values == {k: v for k, v in before.items() if k != key}
  after = params.values.copy()
  for gate in ['engaged', 'started', 'IsOnroad']:
    if gate == 'IsOnroad':
      params.values[gate] = True
    else:
      setattr(state, gate, True)
    reset.prompt_steering_reset(key)
    assert 'vehicle off' in shown[-1].message
    if gate == 'IsOnroad':
      params.values[gate] = False
    else:
      setattr(state, gate, False)
    assert params.values == after


@pytest.mark.parametrize('entry', ['device', 'model'])
def test_camera_confirmation_entry_points(monkeypatch: Any, entry: str) -> None:
  from openpilot.selfdrive.ui.layouts.settings import device
  from openpilot.selfdrive.ui.sunnypilot.layouts.settings import models

  module = device if entry == 'device' else models
  params = Params()
  state = SimpleNamespace(params=params, engaged=False)
  shown: list[Any] = []
  monkeypatch.setattr(module, 'ui_state', state)
  monkeypatch.setattr(module, 'gui_app', SimpleNamespace(push_widget=shown.append))
  monkeypatch.setattr(module, 'ConfirmDialog', lambda message, action, callback: SimpleNamespace(message=message, callback=callback))
  if entry == 'device':
    device.DeviceLayout._reset_calibration_prompt(cast(Any, SimpleNamespace(_params=params, _update_calib_description=lambda: None)))
  else:
    models.ModelsLayout._show_reset_params_dialog()
  assert 'camera calibration' in shown[-1].message.lower()
  before = params.values.copy()
  callback = shown[-1].callback
  callback(DialogResult.CANCEL)
  assert params.values == before
  state.engaged = True
  callback(DialogResult.CONFIRM)
  assert params.values == before
  state.engaged = False
  callback(DialogResult.CONFIRM)
  expected = {k: v for k, v in before.items() if k != 'CalibrationParams'}
  if entry == 'device':
    expected['OnroadCycleRequested'] = True
  assert params.values == expected


@pytest.mark.parametrize('key, field', [('LiveDelay', 'liveDelay'), ('LiveTorqueParameters', 'liveTorqueParameters')])
def test_steering_progress_moves_to_its_own_reset_description(key: str, field: str) -> None:
  from cereal import messaging
  params = Params()
  msg = messaging.new_message(field)
  getattr(msg, field).calPerc = 73
  params.values[key] = msg.to_bytes()
  assert '73%' in reset.steering_learning_description(cast(Any, params), key)
  params.values[key] = b'invalid'
  assert 'Relearning takes time' in reset.steering_learning_description(cast(Any, params), key)
