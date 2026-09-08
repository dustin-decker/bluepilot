#!/usr/bin/env python3
"""Grouped calibration uncertainty and held-out steering-response identification."""

from typing import Any
from collections.abc import Sequence
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from openpilot.tools.autocal_analysis import COLUMNS


def fit_factors(samples: Sequence[Sequence[Any]], platform_gain: float = 0.95) -> list[float] | None:
  from opendbc.sunnypilot.car.ford.angle_autocal import AngleFactorEstimator
  from opendbc.sunnypilot.car.ford.values_ext import LOW_ANCHOR_BASE

  estimator = AngleFactorEstimator(platform_gain)
  for _, _, v, k, measured, gain, weight in samples:
    estimator.add_sample(v, k, measured, gain, weight)
  result = estimator.solve()
  if result is None:
    return None
  return [result[2]['anchor_low'] / LOW_ANCHOR_BASE, result[2]['anchor_high'] / platform_gain]


def confidence(path: Path, output: Path, platform_gain: float) -> None:
  samples = json.loads(path.read_text())
  if not np.isfinite(platform_gain) or platform_gain <= 0:
    raise ValueError('Platform gain must be finite and positive')
  if not samples or any(len(s) != 7 or not np.isfinite(s[1:]).all() or s[6] <= 0 for s in samples):
    raise ValueError('Expected nonempty route/time/speed/command/measured/gain/positive-weight rows')

  def fit(rows: Sequence[Sequence[Any]]) -> list[float] | None:
    return fit_factors(rows, platform_gain)

  groups: list[list[Any]] = []
  for s in samples:
    if not groups or s[0] != groups[-1][-1][0] or s[1] - groups[-1][-1][1] > 2:
      groups.append([])
    groups[-1].append(s)
  rng = np.random.default_rng(20260907)
  bootstrap = [fit([s for i in rng.integers(0, len(groups), len(groups)) for s in groups[i]]) for _ in range(2000)]
  good = [b for b in bootstrap if b is not None]
  routes = sorted({s[0] for s in samples})
  result: dict[str, Any] = {
    'samples': len(samples),
    'groups': len(groups),
    'routes': routes,
    'fit_unclamped': fit(samples),
    'group_bootstrap_95_percent': np.percentile(good, [2.5, 97.5], axis=0).tolist() if good else None,
    'unidentifiable_resamples': len(bootstrap) - len(good),
    'leave_route_out': {
      r: {
        'train': fit([s for s in samples if s[0] != r]),
        'held_out': fit([s for s in samples if s[0] == r]),
        'held_out_updates': sum(s[0] == r for s in samples),
      }
      for r in routes
    },
    'directions': {str(sign): fit([s for s in samples if np.sign(s[3]) == sign]) for sign in [-1, 1]},
    'speed_groups': {
      name: {'updates': sum(lo <= s[2] < hi for s in samples), 'fit': fit([s for s in samples if lo <= s[2] < hi])}
      for name, lo, hi in [('under_45mph', 0, 20.1168), ('45_to_60mph', 20.1168, 26.8224), ('over_60mph', 26.8224, 100)]
    },
    'notes': 'Unclamped factors show instability. Bootstrap groups are runs separated by >2s, not guaranteed distinct physical turns.',
  }
  output.write_text(json.dumps(result, indent=2))
  print(json.dumps(result, indent=2))


def design(window: np.ndarray, delay: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
  """ARX(2): commanded path angle -> wheel angle; subtract each window mean to remove static bias."""
  u = window[:, COLUMNS.index('command')]
  y = np.deg2rad(window[:, COLUMNS.index('steering')] - window[:, COLUMNS.index('offset')])
  u, y = u - u.mean(), y - y.mean()
  start = max(delay, 2)
  x = np.column_stack([y[start - 1 : -1], y[start - 2 : -2], u[start - delay : len(u) - delay or None]])
  return x, y[start:], u, y, start


def response_fit(windows: np.ndarray) -> dict[str, Any] | None:
  best = None
  for delay in range(1, 11):
    xy = [design(w, delay) for w in windows]
    x, y = np.vstack([v[0] for v in xy]), np.concatenate([v[1] for v in xy])
    coefficient = np.linalg.lstsq(x, y, rcond=None)[0]
    poles = np.roots([1, -coefficient[0], -coefficient[1]])
    if max(abs(poles)) >= 0.999 or coefficient[2] <= 0:
      continue
    loss = float(np.mean((x @ coefficient - y) ** 2))
    if best is None or loss < best['training_mse']:
      best = {
        'delay_samples': delay,
        'delay_s': delay * 0.05,
        'coefficients': coefficient.tolist(),
        'pole_magnitudes': abs(poles).tolist(),
        'decay_time_constants_s': (-0.05 / np.log(abs(poles))).tolist(),
        'dc_gain': float(coefficient[2] / (1 - coefficient[0] - coefficient[1])),
        'training_mse': loss,
      }
  return best


def predict(window: np.ndarray, model: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, int]:
  x, y, u, centered, start = design(window, model['delay_samples'])
  a, b, c = model['coefficients']
  predicted = centered.copy()
  for i in range(start, len(u)):
    predicted[i] = a * predicted[i - 1] + b * predicted[i - 2] + c * u[i - model['delay_samples']]
  return predicted[start:], y, start


def validate(windows: np.ndarray, model: dict[str, Any]) -> dict[str, float]:
  errors, static, persistence, target = [], [], [], []
  for w in windows:
    pred, y, start = predict(w, model)
    u = w[:, COLUMNS.index('command')]
    u = u - u.mean()
    errors.extend((pred - y) ** 2)
    target.extend(y * y)
    static.extend((u[start:] * model['dc_gain'] - y) ** 2)
    # Fixed-at-window-start baseline, not one-step persistence fed the future wheel trace.
    persistence.extend((y[0] - y) ** 2)
  return {
    'free_run_rmse_deg': float(np.rad2deg(np.sqrt(np.mean(errors)))),
    'static_rmse_deg': float(np.rad2deg(np.sqrt(np.mean(static)))),
    'fixed_initial_rmse_deg': float(np.rad2deg(np.sqrt(np.mean(persistence)))),
    'variance_explained': float(1 - np.mean(errors) / np.mean(target)),
  }


def identify(path: Path, metadata: Path, output: Path) -> None:
  from opendbc.car.vehicle_model import VehicleModel, create_dyn_state_matrices

  data = np.load(path)
  routes, windows = data['routes'], data['windows']
  meta = json.loads(metadata.read_text())
  # No holdout sharing across a route; selection uses train only. Do not fit through saturation.
  usable = windows[:, :, COLUMNS.index('limited')].sum(axis=1) == 0
  usable &= windows[:, :, COLUMNS.index('command')].std(axis=1) > 0.0005
  routes, windows = routes[usable], windows[usable]
  result: dict[str, Any] = {
    'windows': len(windows),
    'dt_s': 0.05,
    'route_holdouts': {},
    'model': None,
    'limitations': 'Observational ARX; held-out window means removed. Transient-only, not causal or DC validation; no camera/planner.',
  }
  for route in np.unique(routes):
    train, test = windows[routes != route], windows[routes == route]
    if not len(train) or not len(test):
      continue
    model = response_fit(train)
    result['route_holdouts'][str(route)] = {
      'train_windows': len(train),
      'test_windows': len(test),
      'model': model,
      'validation': validate(test, model) if model else None,
    }
  result['speed_holdouts'] = {}
  speeds = windows[:, :, 1].mean(axis=1)
  for label, lo, hi in [('under_45mph', 0, 20.1168), ('45_to_60mph', 20.1168, 26.8224), ('over_60mph', 26.8224, 100)]:
    mask = (speeds >= lo) & (speeds < hi)
    folds = {}
    for route in np.unique(routes[mask]):
      train, test = windows[mask & (routes != route)], windows[mask & (routes == route)]
      fitted = response_fit(train) if len(train) >= 5 and len(test) >= 2 else None
      folds[str(route)] = {'train_windows': len(train), 'test_windows': len(test), 'model': fitted, 'validation': validate(test, fitted) if fitted else None}
    result['speed_holdouts'][label] = folds
  model = response_fit(windows) if len(windows) else None
  result['model'] = model
  # Cascade actuator -> existing dynamic bicycle VehicleModel on held-out route, without fitting it to that route.
  for route, fold in result['route_holdouts'].items():
    if fold['model'] is None:
      continue
    vm = VehicleModel(SimpleNamespace(**meta['routes'][route]['car_params']))
    errors, baseline = [], []
    for w in windows[routes == route]:
      pred, _, start = predict(w, fold['model'])
      speed = float(np.mean(w[:, 1]))
      vm.update_params(float(np.median(w[:, 14])), float(np.median(w[:, 13])))
      A, B = create_dyn_state_matrices(speed, vm)
      # Trapezoidal integration avoids unstable Euler steps; state starts in measured steady turn.
      steady_angle = float(np.deg2rad(np.mean(w[:, 4] - w[:, 12])))
      state = vm.steady_state_sol(steady_angle, speed, float(np.mean(w[:, 11]))).flatten()
      I = np.eye(2)
      Ad = np.linalg.solve(I - 0.025 * A, I + 0.025 * A)
      Bd = np.linalg.solve(I - 0.025 * A, 0.05 * B)
      for i, angle in enumerate(pred, start):
        state = Ad @ state + Bd @ np.array([angle + steady_angle, w[i, 11]])
        if i > start + 40:
          errors.append((state[1] - w[i, 3]) ** 2)
          baseline.append((np.mean(w[:, 3]) - w[i, 3]) ** 2)
    fold['vehicle_model_yaw_rmse_rad_s'] = float(np.sqrt(np.mean(errors)))
    fold['constant_mean_yaw_rmse_rad_s'] = float(np.sqrt(np.mean(baseline)))
  result['validated'] = bool(result['route_holdouts']) and all(
    f['validation']
    and f['validation']['variance_explained'] > 0.5
    and f['validation']['free_run_rmse_deg'] < f['validation']['static_rmse_deg']
    and f['vehicle_model_yaw_rmse_rad_s'] < f['constant_mean_yaw_rmse_rad_s']
    for f in result['route_holdouts'].values()
  )
  output.write_text(json.dumps(result, indent=2))
  print(json.dumps(result, indent=2))


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest='action', required=True)
  p = sub.add_parser('confidence')
  p.add_argument('samples', type=Path)
  p.add_argument('--output', type=Path, required=True)
  p.add_argument('--platform-gain', type=float, required=True)
  p = sub.add_parser('identify')
  p.add_argument('windows', type=Path)
  p.add_argument('--metadata', type=Path, required=True)
  p.add_argument('--output', type=Path, required=True)
  a = parser.parse_args()
  if a.action == 'confidence':
    confidence(a.samples, a.output, a.platform_gain)
  else:
    identify(a.windows, a.metadata, a.output)


if __name__ == '__main__':
  main()
