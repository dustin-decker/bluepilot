#!/usr/bin/env python3
"""Offline Ford CAN-FD response diagnostics. Writes local evidence, never vehicle params."""

from typing import Any
from collections.abc import Iterator
import argparse
import json
from pathlib import Path

import numpy as np

# Scalar cache: no pickle, historical Python classes, or private params required to read it.
COLUMNS = 't speed accel yaw steering torque pressed command active low high roll offset ratio stiffness limited'.split()


def extract(inventory: Path, logs: Path, output: Path) -> None:
  from openpilot.tools.lib.logreader import LogReader

  rows = sorted((json.loads(s) for s in inventory.read_text().splitlines()), key=lambda r: r['local_start'])
  output.mkdir(parents=True, exist_ok=True)
  route: str | None = None
  latest: dict[str, Any] = {}
  stamps: dict[str, float] = {}
  values: list[list[float]] = []
  metadata: dict[str, Any] = {
    'columns': COLUMNS,
    'segments': len(rows),
    'routes': {},
    'freshness_s': 0.25,
    'command_units': 'Ford transmitted path angle (rad), native sign',
    'sampling': 'CAN 982, nominal 20 Hz',
  }

  def save() -> None:
    if route is not None:
      np.savez_compressed(output / f'{route}.npz', data=np.asarray(values).reshape(-1, len(COLUMNS)))

  for index, row in enumerate(rows):
    current = row['segment'].rsplit('--', 1)[0]
    if current != route:
      save()
      route, latest, stamps, values = current, {}, {}, []
      metadata['routes'][route] = {'local_start': row['local_start']}
    for event in LogReader(str(logs / row['segment'] / 'rlog.zst'), sort_by_time=True):
      name, t = event.which(), event.logMonoTime * 1e-9
      if name == 'initData':
        metadata['routes'][route]['commit'] = event.initData.gitCommit
      if name == 'carParams':
        metadata['routes'][route]['car_params'] = {
          name: getattr(event.carParams, name)
          for name in ('mass', 'rotationalInertia', 'wheelbase', 'centerToFront', 'steerRatioRear', 'steerRatio', 'tireStiffnessFront', 'tireStiffnessRear')
        }
      if name in ('carState', 'carControl', 'controllerStateBP', 'liveParameters'):
        latest[name], stamps[name] = getattr(event, name), t if event.valid else -np.inf
      if name != 'sendcan':
        continue
      for frame in event.sendcan:
        if frame.address != 982 or frame.src != 0:
          continue
        if any(name not in latest or not 0 <= t - stamps[name] < 0.25 for name in ('carState', 'carControl', 'controllerStateBP', 'liveParameters')):
          continue
        cs, cc, bp, lp = (latest[n] for n in ('carState', 'carControl', 'controllerStateBP', 'liveParameters'))
        b = bytes(frame.dat)
        angle = (((b[3] & 31) << 6) | (b[4] >> 2)) * 0.0005 - 0.5
        active = ((b[0] >> 4) & 7) != 0 and cc.latActive and str(bp.activeLateralMode) == 'angle'
        values.append(
          [
            t,
            cs.vEgoRaw,
            cs.aEgo,
            cs.yawRate,
            cs.steeringAngleDeg,
            cs.steeringTorque,
            cs.steeringPressed,
            angle,
            active and not bp.humanTurnLateralPaused and not bp.stallBlipActive,
            bp.bmsLowSpeedAdjustmentFactor,
            bp.bmsHighSpeedAdjustmentFactor,
            lp.roll,
            lp.angleOffsetDeg,
            lp.steerRatio,
            lp.stiffnessFactor,
            bp.angleRateLimited or bp.curvatureDeviationLimited or bp.angleSaturated,
          ]
        )
    if index % 30 == 0:
      print(f'{index}/{len(rows)} segments', flush=True)
  save()
  (output / 'metadata.json').write_text(json.dumps(metadata, indent=2))


def windows(data: np.ndarray, seconds: float = 20, min_speed: float = 15.0, driver_clear: bool = True) -> Iterator[np.ndarray]:
  """Disjoint continuous windows; 3 seconds clear of any driver input, including light grip."""
  d = dict(zip(COLUMNS, data.T, strict=True))
  clean = np.isfinite(data).all(axis=1) & (d['active'] > 0) & (d['speed'] >= min_speed)
  clean &= (np.abs(d['accel']) <= 2.5) & (np.abs(d['yaw'] * d['speed']) < 2.5)
  last_touch = d['t'][0] if len(data) else 0.0
  run: list[int] = []
  for i in range(len(data)):
    if d['pressed'][i] or abs(d['torque'][i]) >= 0.5:
      last_touch = d['t'][i]
    if not clean[i] or (driver_clear and d['t'][i] - last_touch < 3) or (i and not 0.025 < d['t'][i] - d['t'][i - 1] < 0.075):
      run = []
      continue
    run.append(i)
    if len(run) == round(seconds * 20):
      yield data[run]
      run = []


def spectrum(y: np.ndarray, dt: float = 0.05) -> tuple[float, float, float]:
  x = np.arange(len(y))
  y = y - np.polyval(np.polyfit(x, y, 1), x)
  fft = np.fft.rfft(y * np.hanning(len(y)))
  f = np.fft.rfftfreq(len(y), dt)
  band = (f >= 0.2) & (f <= 2)
  peak = np.flatnonzero(band)[np.argmax(np.abs(fft[band]))]
  return float(f[peak]), float(np.sqrt(np.mean(y * y))), float(abs(fft[peak]) ** 2 / max(np.sum(abs(fft[band]) ** 2), 1e-20))


def diagnose(cache: Path, output: Path) -> None:
  output.mkdir(parents=True, exist_ok=True)
  episodes: list[dict[str, Any]] = []
  response_windows: list[tuple[str, Any]] = []
  for path in sorted(cache.glob('*.npz')):
    data = np.load(path)['data']
    response_windows.extend((path.stem, w) for w in windows(data, seconds=5))
    for window in windows(data, driver_clear=False):
      d = dict(zip(COLUMNS, window.T, strict=True))
      # Straight-ish road criterion fixed before ranking: mean lateral acceleration <0.3 m/s².
      if np.mean(d['speed']) < 26.82 or abs(np.mean(d['yaw'] * d['speed'])) >= 0.3:
        continue
      f, rms, concentration = spectrum(d['steering'])
      cf, crms, _ = spectrum(d['command'])
      episodes.append(
        {
          'route': path.stem,
          't': float(d['t'][0]),
          'speed_mph': float(np.mean(d['speed']) * 2.23694),
          'wheel_rms_deg': rms,
          'frequency_hz': f,
          'spectral_concentration': concentration,
          'command_rms_rad': crms,
          'command_frequency_hz': cf,
          'pressed_fraction': float(np.mean(d['pressed'])),
          'light_grip_fraction': float(np.mean(abs(d['torque']) >= 0.5)),
          'high_factor': float(np.median(d['high'])),
          'low_factor': float(np.median(d['low'])),
          'roll_deg': float(np.rad2deg(np.mean(d['roll']))),
          'offset_deg': float(np.mean(d['offset'])),
          'scaled_command_threshold_crossings_proxy': int(np.sum(np.diff(np.abs(d['command']) / np.maximum(d['speed'], 1) > 0.0005) != 0)),
        }
      )
  episodes.sort(key=lambda x: x['wheel_rms_deg'], reverse=True)
  (output / 'oscillation.json').write_text(json.dumps({'window_s': 20, 'windows': episodes}, indent=2))
  # Cache analysis windows once for response identification; each row remains route-grouped.
  np.savez_compressed(
    output / 'response_windows.npz',
    routes=np.array([r for r, _ in response_windows]),
    windows=np.array([w for _, w in response_windows]).reshape(-1, 100, len(COLUMNS)),
  )
  import matplotlib

  matplotlib.use('Agg')
  import matplotlib.pyplot as plt

  for rank, episode in enumerate(episodes[:5]):
    data = np.load(cache / f'{episode["route"]}.npz')['data']
    w = data[(data[:, 0] >= episode['t']) & (data[:, 0] < episode['t'] + 20)]
    fig, axes = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
    for ax, cols, label in zip(axes, [(4,), (7, 3), (9, 10)], ['Wheel (deg)', 'Path angle / yaw (rad, rad/s)', 'Adjustment factors'], strict=True):
      for col in cols:
        ax.plot(w[:, 0] - w[0, 0], w[:, col], label=COLUMNS[col])
      ax.set_ylabel(label)
      ax.legend()
      ax.grid(alpha=0.3)
    axes[-1].set_xlabel('Seconds')
    fig.suptitle(f'{episode["route"]} @ {episode["t"]:.2f}s; {episode["speed_mph"]:.1f} mph')
    fig.tight_layout()
    fig.savefig(output / f'oscillation-{rank + 1}.png')
    plt.close(fig)
  print(json.dumps({'straight_windows': len(episodes), 'response_windows': len(response_windows), 'largest': episodes[:3]}, indent=2))


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest='action', required=True)
  p = sub.add_parser('extract')
  p.add_argument('--inventory', type=Path, required=True)
  p.add_argument('--logs', type=Path, required=True)
  p.add_argument('--output', type=Path, required=True)
  p = sub.add_parser('diagnose')
  p.add_argument('--cache', type=Path, required=True)
  p.add_argument('--output', type=Path, required=True)
  a = parser.parse_args()
  if a.action == 'extract':
    extract(a.inventory, a.logs, a.output)
  else:
    diagnose(a.cache, a.output)


if __name__ == '__main__':
  main()
