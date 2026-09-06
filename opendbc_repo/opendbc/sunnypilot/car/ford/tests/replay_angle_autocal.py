"""Read-only counterfactual smoke replay of the angle strategy and armed autocal glue.

Run with local rlog paths. Reuses the unit-test harness: no sockets, CAN output or real
Params writes. Historical response belongs to the OLD controller, so fitted factors
are deliberately not reported as valid calibration or as closed-loop validation.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
from types import SimpleNamespace

from openpilot.tools.lib.logreader import LogReader
from opendbc.sunnypilot.car.ford.tests.test_angle_autocal import _MockParams
from opendbc.sunnypilot.car.ford.tests.test_lateral_angle_ext import _Harness


def replay(paths, strength=1.0):
  latest, stamps = {}, {}
  ext = None
  counts = Counter()
  last = None
  params = _MockParams({'FordAngleAutoCal': True, 'FordAngleAutoCalLock': False})
  for path in paths:
    if not Path(path).is_file():
      raise ValueError(f'Expected a local rlog file: {path}')
    for msg in LogReader(path, sort_by_time=True):
      name = msg.which()
      if name not in ('carParams', 'carParamsSP', 'carState', 'carControl', 'modelV2', 'liveParameters', 'liveDelay', 'controllerStateBP'):
        continue
      if not msg.valid and name not in ('carParams', 'carParamsSP'):
        latest.pop(name, None)
        continue
      latest[name], stamps[name] = getattr(msg, name), msg.logMonoTime
      if name != 'carControl' or not all(k in latest for k in ('carParams', 'carState', 'modelV2', 'liveDelay')):
        continue
      if last is not None and msg.logMonoTime - last < 49_000_000:
        continue
      if ext is None:
        ext = _Harness(latest['carParams'], latest.get('carParamsSP'))
        ext.update_angle_params(params)
        ext.smoother.configure(True, strength)
        ext.autocal_ctl.poll_params(params, 1.0, 1.0, ext.path_angle_gain_highC_highV)
      if (last is not None and msg.logMonoTime - last > 100_000_000) or any(
        msg.logMonoTime - stamps[k] > 500_000_000 for k in ('carState', 'modelV2', 'liveDelay')):
        ext.autocal_ctl.idle()
        counts['gaps_or_stale'] += 1
        last = msg.logMonoTime
        continue
      last = msg.logMonoTime
      ext.model = latest['modelV2']
      ext.lp = latest.get('liveParameters')
      if ext.lp is not None:
        ext.VM.update_params(max(ext.lp.stiffnessFactor, 0.1), max(ext.lp.steerRatio, 0.1))
      ext.sm = {'liveDelay': latest['liveDelay']}
      cs = SimpleNamespace(out=latest['carState'], lat_ctl_lim_stat=0)
      result = ext.update_angle_strategy(latest['carControl'], cs, latest['carControl'].actuators, latest['carParams'])
      assert all(math.isfinite(getattr(result, key)) for key in ('apply_curvature', 'path_angle', 'path_offset', 'curvature_rate'))
      assert abs(result.path_angle) <= 0.5
      counts['frames'] += 1
      counts['armed_frames'] += int(ext.autocal_ctl.enabled)
      counts['delay_estimated_frames'] += int(str(latest['liveDelay'].status) == 'estimated')
      if 'controllerStateBP' in latest:
        counts['recorded_mode_' + str(latest['controllerStateBP'].activeLateralMode)] += 1
      counts['lat_active'] += int(latest['carControl'].latActive)
      counts['rate_limited'] += int(ext.bp_angle_rate_limited)
      counts['deviation_limited'] += int(ext.bp_curvature_deviation_limited)
      counts['human_paused'] += int(ext.angle_human_turn_active)
      counts['autocal_errors'] += int(bool(params.get('FordAngleAutoCalError')))
  assert counts['frames'] > 0, 'No replayable frames'
  assert counts['autocal_errors'] == 0, params.get('FordAngleAutoCalError')
  counts['counterfactual_admitted_samples'] = ext.autocal_ctl.pipeline.est.n if ext.autocal_ctl.pipeline else 0
  result = dict(counts)
  if ext.autocal_ctl.pipeline:
    result['quality_rejections'] = ext.autocal_ctl.pipeline.quality.counters
  return result


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('rlogs', nargs='+')
  parser.add_argument('--strength', type=float, default=1.0)
  args = parser.parse_args()
  print(json.dumps(replay(args.rlogs, args.strength), sort_keys=True))
