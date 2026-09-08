#!/usr/bin/env python3
"""Diagnostic angle-strategy/actuator/VehicleModel feedback loop; no camera/planner simulation."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, TypedDict, cast

import numpy as np
from numpy.typing import NDArray
from opendbc.car.vehicle_model import VehicleModel, create_dyn_state_matrices
from opendbc.sunnypilot.car.ford.tests.test_lateral_angle_ext import _Harness, _Model
from opendbc.sunnypilot.car.ford.tests.test_angle_autocal import _MockParams
from openpilot.tools.lib.logreader import LogReader
from openpilot.tools.lib.source_provenance import source_provenance


class ResponseModel(TypedDict):
  coefficients: list[float]
  delay_samples: int


class FittedResult(TypedDict):
  model: ResponseModel | None
  validated: bool


def simulate(cp: Any, cp_sp: Any, model: ResponseModel, speed: float, high: float) -> NDArray[np.float64]:
  ext = _Harness(cp, cp_sp)
  params = _MockParams(
    {
      'FordAngleAutoCal': False,
      'FordAngleAutoCalLock': False,
      'FordLowSpeedFactor_ang': 1.13,
      'FordHighSpeedFactor_ang': high,
      'FordHighSpeedDampening_ang': 1.0,
      'FordAngleSmoothing': False,
      'FordAngleSmoothStrength': 1.0,
    }
  )
  ext.update_angle_params(params)
  ext.smoother.configure(False, 1.0)
  cast(Any, ext).sm = {'liveDelay': NS(status='estimated', lateralDelay=0.33)}
  cast(Any, ext).model = _Model()
  vm = VehicleModel(cp)
  cast(Any, ext).lp = NS(stiffnessFactor=1.0, steerRatio=cp.steerRatio, angleOffsetDeg=0.0, roll=0.0)
  A, B = create_dyn_state_matrices(speed, vm)
  I = np.eye(2)
  Ad, Bd = np.linalg.solve(I - 0.025 * A, I + 0.025 * A), np.linalg.solve(I - 0.025 * A, 0.05 * B)
  state = np.zeros(2)
  wheel, commands, rows = [0.0, 0.0], [0.0] * (model['delay_samples'] + 1), []
  a, b, c = model['coefficients']
  for i in range(800):
    t = i * 0.05
    # Modest curve, straight, then small periodic correction. Reference is prescribed, not camera-generated.
    target = 0.001 * np.clip((t - 5) / 2, 0, 1) * np.clip((20 - t) / 2, 0, 1)
    if t > 25:
      target = 0.0003 * np.sin(2 * np.pi * 0.8 * (t - 25))
    cast(Any, ext.model).orientationRate.z = [target * speed] * 33
    cs = NS(
      out=NS(
        vEgoRaw=speed,
        vEgo=speed,
        yawRate=state[1],
        steeringAngleDeg=np.rad2deg(wheel[-1]),
        steeringPressed=False,
        steeringTorque=0.0,
        aEgo=0.0,
        wheelSpeeds=NS(fl=speed, fr=speed, rl=speed, rr=speed),
      ),
      lat_ctl_lim_stat=0,
    )
    for _ in range(5):
      ext.update_angle_params(params)
    result = ext.update_angle_strategy(NS(latActive=True), cs, NS(curvature=target), cp)
    commands.append(-result.path_angle)  # Ford wire sign, matching response identification.
    angle = a * wheel[-1] + b * wheel[-2] + c * commands[-1 - model['delay_samples']]
    wheel.append(angle)
    state = Ad @ state + Bd @ np.array([angle, 0.0])
    rows.append([t, target, -state[1] / speed, commands[-1], np.rad2deg(angle)])
  data = np.asarray(rows)
  if not np.isfinite(data).all():
    raise ValueError('Simulation diverged to nonfinite values')
  return data


def main() -> None:
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('--log', required=True)
  p.add_argument('--model', type=Path, required=True)
  p.add_argument('--output', type=Path, required=True)
  p.add_argument('--revision', required=True)
  args = p.parse_args()
  source = source_provenance(args.revision)
  messages = list(LogReader(args.log))
  cp = next(m.carParams for m in messages if m.which() == 'carParams')
  cp_sp = next(m.carParamsSP for m in messages if m.which() == 'carParamsSP')
  fitted = cast(FittedResult, json.loads(args.model.read_text()))
  if fitted['model'] is None:
    raise ValueError('No fitted response model')
  args.output.mkdir(parents=True, exist_ok=True)
  results: dict[str, Any] = {
    'revision': source['commit'] or args.revision,
    'source_provenance': source,
    'plant_validated': fitted['validated'],
    'scope': 'Angle strategy + fitted actuator + VehicleModel with yaw feedback. Prescribed reference; no camera/planner or PSCM transitions.',
    'settings_recommendation': False,
    'cases': [],
  }
  for speed in [15.0, 26.82, 31.29]:
    for high in [1.0, 1.03, 1.1]:
      data = simulate(cp, cp_sp, fitted['model'], speed, high)
      np.savez_compressed(args.output / f'{speed}-{high}.npz', data=data)
      results['cases'].append(
        {
          'speed_mps': speed,
          'high': high,
          'low': 1.13,
          'curvature_rmse': float(np.sqrt(np.mean((data[:, 1] - data[:, 2]) ** 2))),
          'wheel_peak_deg': float(np.max(abs(data[:, 4]))),
        }
      )
  (args.output / 'result.json').write_text(json.dumps(results, indent=2))
  print(json.dumps(results, indent=2))


if __name__ == '__main__':
  main()
