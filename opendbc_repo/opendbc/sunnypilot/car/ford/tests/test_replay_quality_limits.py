"""Offline quality overrides must reject invalid values and never leak into later runs."""
from unittest.mock import patch

import pytest

from opendbc.sunnypilot.car.ford import angle_autocal
from opendbc.sunnypilot.car.ford.tests import replay_angle_autocal


def test_quality_limits_are_validated_and_restored() -> None:
  original = angle_autocal.ROUGH_RMS_MAX, angle_autocal.MAX_LONG_ACCEL
  for value in (0, -1, float('nan'), float('inf')):
    for argument in ('rough_rms_max', 'long_accel_max'):
      with pytest.raises(ValueError, match='finite and positive'):
        replay_angle_autocal.replay([], **{argument: value})

  def fail(*args: object) -> None:
    assert (angle_autocal.ROUGH_RMS_MAX, angle_autocal.MAX_LONG_ACCEL) == (0.003, 3.0)
    raise RuntimeError('replay failed')

  with patch.object(replay_angle_autocal, '_replay', fail):
    with pytest.raises(RuntimeError, match='replay failed'):
      replay_angle_autocal.replay([], rough_rms_max=0.003, long_accel_max=3.0)
  assert (angle_autocal.ROUGH_RMS_MAX, angle_autocal.MAX_LONG_ACCEL) == original
