"""Native solver checks; build with mise run stops:build before running."""
from collections import deque
from typing import Any

import numpy as np
import pytest
from cereal import log

from .core import MAX_SPEED, Sample, StopController
from .types import Target
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc, T_IDXS
from openpilot.selfdrive.controls.lib.drive_helpers import get_accel_from_plan


def simulate(mph: float, delay: float = 0.3) -> dict[str, Any]:
  """Illustrative longitudinal plant, not measured Ford brake response."""
  speed, acceleration, travelled = mph * 0.44704, 0.0, 0.0
  distance = speed**2 / 2.4 + speed + 50 + 2.6
  controller = StopController(validated=True)
  mpc = LongitudinalMpc()
  radar = log.RadarState.new_message()
  dt = 0.05
  pending = deque([0.0] * max(1, round(delay / dt)))
  history = []
  solver_failures = 0
  planned_speed, planned_accel = speed, 0.0
  for step in range(2400):
    s = Sample(t=step * dt, utc=1, speed=speed, accuracy=0.3)
    target: Target = {'id': 'test', 'distance': distance - travelled, 'accuracy': .3, 'ready': True}
    state = controller.update(s, 'control', target, engaged=True)
    planned_speed = max(0, planned_speed + (speed - planned_speed) * dt / (2 + dt))
    mpc.set_weights(prev_accel_constraint=speed >= 0.01)
    mpc.set_cur_state(planned_speed, planned_accel)
    mpc.update(radar, mph * .44704, stop_distance=state['distance'] if state['apply'] else None)
    solver_failures += mpc.last_solve_status != 0
    old_accel = planned_accel
    planned_accel = float(np.interp(dt, T_IDXS, mpc.a_solution))
    planned_speed += dt * (old_accel + planned_accel) / 2
    command, should_stop = get_accel_from_plan(mpc.v_solution, mpc.a_solution, T_IDXS, action_t=delay + dt, vEgoStopping=0.5)
    if state['hold'] or should_stop:
      command = min(command, -0.5)
    pending.append(command)
    acceleration += (pending.popleft() - acceleration) * dt / 0.2
    next_speed = max(0, speed + acceleration * dt)
    travelled += (speed + next_speed) / 2 * dt
    speed = next_speed
    if speed == 0:
      acceleration = 0.0
    history.append([s.t, distance - travelled, speed, acceleration, state['hold'], command, planned_speed, planned_accel])
    if state['hold'] and speed == 0 and step > 10:
      break
  return {'mph': mph, 'delay': delay, 'seconds': history[-1][0], 'remaining_m': history[-1][1],
          'stopped': speed == 0, 'held': bool(history[-1][4]),
          'minimum_accel': min(p[3] for p in history), 'solver_failures': solver_failures, 'history': history}


def test_off_observe_have_identical_mpc_results() -> None:
  radar = log.RadarState.new_message()
  baseline, observe = LongitudinalMpc(), LongitudinalMpc()
  controller = StopController()
  speed, acceleration = 20.0, 0.0
  for i in range(50):
    state = controller.update(Sample(t=i * .05, utc=1, speed=speed), 'observe',
                              {'id': 'test', 'distance': 100, 'accuracy': .3, 'ready': True}, engaged=True)
    assert not state['apply']
    baseline.set_cur_state(speed, acceleration)
    observe.set_cur_state(speed, acceleration)
    baseline.update(radar, 20)
    observe.update(radar, 20, stop_distance=state['distance'] if state['apply'] else None)
    assert baseline.last_solve_status == observe.last_solve_status == 0
    np.testing.assert_array_equal(baseline.a_solution, observe.a_solution)
    np.testing.assert_array_equal(baseline.params, observe.params)
    speed = float(np.interp(.05, T_IDXS, baseline.v_solution))
    acceleration = float(np.interp(.05, T_IDXS, baseline.a_solution))


@pytest.mark.parametrize('mph', [10, 25, 45, 65, 70])
def test_closed_loop_stops(mph: float) -> None:
  result = simulate(mph)
  assert result['stopped'] and result['held'], result
  assert result['solver_failures'] == 0, result
  assert result['remaining_m'] > 0, result
  assert mph * .44704 <= MAX_SPEED


def test_closer_lead_keeps_stronger_constraint() -> None:
  radar = log.RadarState.new_message()
  radar.leadOne.status = True
  radar.leadOne.dRel = 10
  radar.leadOne.vLead = 0
  radar.leadOne.aLeadTau = 1.5
  baseline, constrained = LongitudinalMpc(), LongitudinalMpc()
  for mpc in (baseline, constrained):
    mpc.set_cur_state(5, 0)
  baseline.update(radar, 10)
  constrained.update(radar, 10, stop_distance=200)
  assert baseline.last_solve_status == constrained.last_solve_status == 0
  np.testing.assert_array_equal(baseline.params, constrained.params)
  np.testing.assert_array_equal(baseline.a_solution, constrained.a_solution)
