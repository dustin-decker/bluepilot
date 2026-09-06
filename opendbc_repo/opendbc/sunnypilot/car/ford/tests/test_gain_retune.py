"""BluePilot: independent PR #191 gain-model, alignment and closed-loop regressions."""
import json
import math
import random
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest

from opendbc.sunnypilot.car.ford.angle_autocal import AngleFactorEstimator, AutoCalPipeline, Frame, GainSample
from opendbc.sunnypilot.car.ford.angle_autocal_controller import AutoCalController
from opendbc.sunnypilot.car.ford.tests.test_angle_autocal import _MockParams
from opendbc.sunnypilot.car.ford.tests.test_lateral_angle_ext import _Harness, _explorer_cp, _CC, _CS, _Actuators, _FakeParams
from opendbc.sunnypilot.car.ford.values_ext import curve_gain_blend


def gain_model(v, kappa, low, high, damp=1.0):
  """Independent transcription of Praeuner's PR, not the production helper."""
  low_curve = float(np.interp(v, [11.18, 31.29], [1.0, 1.0 * damp]))
  high_curve = float(np.interp(v, [11.18, 31.29], [1.40 * low, 1.20 * 1.05 * high]))
  knee = float(np.interp(v, [8.94, 13.41, 16.54, 31.29], [0.02, 0.0195, 0.018, 0.0035]))
  q = float(np.interp(abs(kappa), [0.0005, knee], [0.0, 1.0]))
  fixed = (1 - q) * low_curve
  return GainSample(fixed + q * high_curve, fixed, q, v)


def frame(v, kappa, measured, gain):
  return Frame(v, kappa, measured, False, False, False, False, 0.0, 0.0, 0.0, 1.0, 1.0, 0.2, gain)


@pytest.mark.parametrize('v', [10.0, 11.18, 16.54, 25.0, 31.29, 35.0])
@pytest.mark.parametrize('kappa', [0.0004, 0.0015, 0.01, 0.025])
def test_strategy_matches_pr191_gain_formula(v, kappa):
  ext = _Harness(_explorer_cp())
  ext.CP.carFingerprint = 'FORD_MUSTANG_MACH_E_MK1'
  ext.update_angle_params(_FakeParams({'FordLowSpeedFactor_ang': 0.9, 'FordHighSpeedFactor_ang': 1.1,
                                      'FordHighSpeedDampening_ang': 0.8}))
  ext.path_angle_blend_ratio = 0.0
  ext.smoothing_enabled = False
  cs = _CS(vEgoRaw=v, vEgo=v)
  with patch.object(ext, 'get_current_curvature', return_value=kappa):
    ext.update_angle_strategy(_CC(), cs, _Actuators(kappa), ext.CP)
  assert ext.curvature_factor == pytest.approx(gain_model(v, kappa, 0.9, 1.1, 0.8).total)
  assert ext._gain_blend == pytest.approx(curve_gain_blend(v, kappa))


def test_partial_gain_recovers_factors_with_changing_settings_and_noise():
  est = AngleFactorEstimator(1.05)
  rng = random.Random(191)
  for low, high, damp in [(1.0, 1.0, 1.0), (1.1, 0.9, 0.7), (0.9, 1.2, 1.15)]:
    for v, magnitude in [(10, 0.012), (15, 0.005), (20, 0.003), (25, 0.002), (32, 0.0015)]:
      for i in range(800):
        k = magnitude if i % 2 else -magnitude
        actual = gain_model(v, k, low, high, damp)
        ideal = gain_model(v, k, 0.93, 1.17, damp)
        measured = k * actual.total / ideal.total * (1 + rng.uniform(-0.002, 0.002))
        assert est.add_sample(v, k, measured, actual, weight=0.05)
  low, high, stats = est.solve()
  assert low == pytest.approx(0.93, abs=0.003)
  assert high == pytest.approx(1.17, abs=0.003)
  assert max(stats['stderr_eff_low'], stats['stderr_eff_high']) < 0.01


@pytest.mark.parametrize('bad', [GainSample(1, 1, 0, 20), GainSample(1, 1, 0.01, 20),
                                GainSample(math.nan, 0, 1, 20), GainSample(1, 0, 1, math.inf)])
def test_unobservable_or_nonfinite_samples_do_not_change_fit(bad):
  est = AngleFactorEstimator(1.05)
  before = est.to_dict()
  assert not est.add_sample(20, 0.002, 0.002, bad)
  assert est.to_dict() == before


def test_lag_alignment_preserves_gain_and_speed_at_issue_time():
  pipe = AutoCalPipeline(1.05)
  seen = []
  original = pipe.est.add_sample

  def capture(v, k, m, gain, weight=1.0):
    seen.append(gain)
    return original(v, k, m, gain, weight)

  with patch.object(pipe.est, 'add_sample', side_effect=capture):
    for i in range(160):
      v = 15 + i * 0.001
      gain = gain_model(v, 0.003, 1, 1)
      pipe.update(frame(v, 0.003, 0.003, gain))
  assert seen
  # Four frames of actuator delay plus twenty of grip holdback, not the current speed.
  assert seen[-1].speed == pytest.approx(15 + (159 - 4 - 20) * 0.001)


@pytest.mark.parametrize('phase', ['collecting', 'locked'])
def test_old_evidence_and_locks_restart_without_resetting_factors(phase):
  donor = AutoCalPipeline(1.05)
  donor.est.add_sample(20, 0.002, 0.002, 1.3)
  old = json.dumps({'v': 1, 'phase': phase, 'pipe': donor.to_dict()})
  params = _MockParams({'FordAngleAutoCal': True, 'FordAngleAutoCalLock': True, 'FordAngleAutoCalState': old,
                        'FordLowSpeedFactor_ang': 0.9, 'FordHighSpeedFactor_ang': 1.1})
  ctl = AutoCalController(0.05)
  ctl.poll_params(params, 0.9, 1.1, 1.05)
  assert ctl.enabled and ctl.pipeline.est.n == 0
  assert params.get('FordLowSpeedFactor_ang') == 0.9
  assert params.get('FordHighSpeedFactor_ang') == 1.1


def test_closed_loop_mixed_gain_converges_without_overshoot():
  """Delay + first-order plant, physical partial-gain evidence, normal admission gates."""
  pipe = AutoCalPipeline(1.05)
  pipe.lock_enabled = False
  applied = (1.0, 1.0)
  changes = []
  for _ in range(12):
    for v, k in [(10, 0.012), (32, -0.0015)]:
      pipe.idle()
      history = []
      measured = 0.0
      for _ in range(1600):
        gain = gain_model(v, k, *applied)
        ideal = gain_model(v, k, 0.93, 1.12)
        history.append(k * gain.total / ideal.total)
        if len(history) > 4:
          measured += 0.2 * (history[-5] - measured)
        pipe.update(replace(frame(v, k, measured, gain), low_factor=applied[0], high_factor=applied[1]))
        rec = pipe.recommend(*applied)
        if rec:
          assert max(abs(a - b) for a, b in zip(applied, rec, strict=True)) <= 0.05 + 1e-9
          applied = rec
          changes.append(rec)
  assert changes
  assert applied[0] == pytest.approx(0.93, abs=0.03)
  assert applied[1] == pytest.approx(1.12, abs=0.03)
  assert all(0.90 <= lo <= 1.0 and 1.0 <= hi <= 1.15 for lo, hi in changes)
