"""Runtime settings and short AutoCal transitions survive the real Cereal publisher."""
import json
from types import SimpleNamespace

from cereal import log
from opendbc.sunnypilot.car.ford.angle_autocal_controller import AutoCalController
from opendbc.sunnypilot.car.ford.angle_smoothing import AngleSmoother
from opendbc.sunnypilot.car.ford.tests.test_angle_autocal import _MockParams
from openpilot.bluepilot.selfdrive.car import bp_card_publisher as publisher


def test_autocal_events_survive_reset_and_publish(monkeypatch):
  p = _MockParams({'FordAngleAutoCal': True, 'FordAngleAutoCalLock': False, 'FordAngleAutoCalState': ''})
  ctl = AutoCalController(.05)
  ctl.poll_params(p, 1.13, 1., .95)
  ctl.poll_params(p, 1.12, 1., .95)
  assert ctl._apply_nudge((1.11, 1.01))
  ctl.poll_params(p, 1.11, 1.01, .95)
  assert sum(e['kind'] == 'external_factor_edit' for e in json.loads(ctl.events_json)['events']) == 1
  p.values['FordAngleAutoCalReset'] = True
  ctl.poll_params(p, 1.11, 1.01, .95)
  events = json.loads(ctl.events_json)['events']
  assert {'arm', 'external_factor_edit', 'nudge', 'reset'} <= {e['kind'] for e in events}
  assert [e['seq'] for e in events] == sorted({e['seq'] for e in events})
  assert all(e['mono_ns'] > 0 for e in events)
  assert next(e for e in events if e['kind'] == 'nudge')['previous'] == [1.12, 1.]

  smoother = AngleSmoother()
  smoother.configure(True, 1.0)
  cc = SimpleNamespace(lateralUncertainty=0., disable_BP_lat_UI=False, primary_lateral_control=1,
                       low_speed_curv_factor=1.13, high_speed_curv_factor=1., lane_change_factor_high_ang=1.,
                       user_dampening_factor=.98, autocal_ctl=ctl, smoother=smoother,
                       autocal_enabled=ctl.enabled, bp_autocal_status=ctl.status)
  captured = []
  pm = SimpleNamespace(send=lambda service, msg: captured.append(msg.to_bytes()))
  ci = SimpleNamespace(CC=cc, CP=None)
  stale = {'bmsLowSpeedAdjustmentFactor': .8, 'bmsHighSpeedAdjustmentFactor': 1.4}
  monkeypatch.setattr(publisher, '_refresh_settings_cache', lambda: stale)
  monkeypatch.setattr(publisher, '_settings_cache', {})
  monkeypatch.setattr(publisher, '_settings_last_read', 0.)
  publisher.publish_controller_state_bp(ci, pm)
  smoother.configure(True, 2.)
  publisher.publish_controller_state_bp(ci, pm)
  for index, raw in enumerate(captured):
    with log.Event.from_bytes(raw) as event:
      state = event.controllerStateBP
      assert state.angleTuningValid
      assert abs(state.bmsLowSpeedAdjustmentFactor - 1.13) < 1e-6
      assert state.bmsHighSpeedAdjustmentFactor == 1.
      assert state.bmsAngleSmoothing
      assert state.bmsAngleSmoothStrength == index + 1.
      assert state.angleSmoothingActive == bool(index)
      assert not state.bmsAngleAutoCalLock
      assert state.angleAutoCalRequested
      assert json.loads(state.angleAutoCalEvents)['events'] == events


def test_event_history_is_bounded_and_errors_are_recorded():
  ctl = AutoCalController(.05)
  for i in range(30):
    ctl.record_settings(low=1. + i/100)
  before = ctl.events_json
  ctl.record_settings(low=1.29)
  assert ctl.events_json == before
  ctl._error('test persistence failure')
  events = json.loads(ctl.events_json)['events']
  assert len(events) == 16 and events[0]['seq'] == 16
  assert events[-1]['kind'] == 'error'


def test_event_bursts_survive_qlog_and_deduplicate_errors():
  ctl = AutoCalController(.05)
  ctl.record_event('arm')
  ctl._error('repeated failure')
  for _ in range(30):
    ctl._error('repeated failure')
  events = json.loads(ctl.events_json)['events']
  assert [e['kind'] for e in events] == ['arm', 'error']
  assert events[-1]['count'] == 31
  frames = [ctl.events_for_log() for _ in range(200)]
  assert sum(bool(f) for f in frames) == 20
  for decimation_phase in range(10):
    sampled = frames[decimation_phase::10]
    assert sum(bool(f) for f in sampled) == 2
  ctl.record_event('reset')
  assert ctl.events_for_log()
  before = ctl.events_json
  ctl.record_event('settings', invalid=object())
  assert ctl.events_json == before
