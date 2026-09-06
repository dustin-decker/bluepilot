#!/usr/bin/env python3
"""Read-only local route selection from qlogs (rlog fallback), emitting JSON lines.

Dates use recorded wall time, never file modification time. Speed coverage counts
valid carState samples in m/s bands. Optional Ford scores count rough candidate
carControl samples, not seconds or accepted calibration evidence. Qlog sampling can
miss brief driver inputs; shortlisted segments must be rechecked with full rlogs.
"""
import argparse
from collections import Counter
from datetime import date, datetime
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


def segment_logs(root):
  from openpilot.tools.lib.route import FileName
  for directory in sorted(Path(root).iterdir()):
    if not directory.is_dir() or not directory.name.rsplit('--', 1)[-1].isdigit():
      continue
    path = next((directory / name for name in (*FileName.QLOG, *FileName.RLOG) if (directory / name).is_file()), None)
    if path is not None:
      yield path


def summarize(events, timezone, day=None, ford_autocal=False):
  counts = Counter()
  cs = None
  cs_time = grip_until = None
  first = last = local_start = None
  min_speed, max_speed = math.inf, -math.inf
  if ford_autocal:
    from opendbc.sunnypilot.car.ford.angle_autocal import (
      MIN_KAPPA, MIN_SPEED, MAX_LAT_ACCEL, TORQUE_GUARD_NM, PRESS_COOLDOWN_S, speed_alpha,
    )
  for msg in events:
    timestamp = msg.logMonoTime
    name = msg.which()
    # initData/carParams are repeated in each segment with their original route-start
    # timestamps. They must not turn a one-minute segment into a cumulative duration.
    if name in ('clocks', 'carState', 'carControl', 'controllerStateBP'):
      first = timestamp if first is None else first
      last = timestamp
    if name == 'clocks' and msg.valid and local_start is None and msg.clocks.wallTimeNanos > 0:
      # Convert this clock observation back to the start of the segment.
      wall_start = msg.clocks.wallTimeNanos - (timestamp - first)
      local_start = datetime.fromtimestamp(wall_start / 1e9, timezone)
      if day is not None and local_start.date() != day:
        return None
    if name not in ('carState', 'carControl', 'controllerStateBP'):
      continue
    if not msg.valid:
      counts['invalid_' + name] += 1
      if name == 'carState':
        cs = None
        grip_until = None
      continue
    if name == 'carState':
      previous_cs_time = cs_time
      cs, cs_time = msg.carState, timestamp
      v = cs.vEgo
      if not math.isfinite(v) or v < 0:
        cs = None
        grip_until = None
        counts['invalid_speed'] += 1
        continue
      min_speed, max_speed = min(min_speed, v), max(max_speed, v)
      counts[['speed_lt10', 'speed_10to20', 'speed_20to30', 'speed_ge30'][min(int(v // 10), 3)]] += 1
      if ford_autocal:
        if grip_until is None or previous_cs_time is None or timestamp - previous_cs_time > 500_000_000:
          grip_until = timestamp + int(PRESS_COOLDOWN_S * 1e9)
        if cs.steeringPressed or not math.isfinite(cs.steeringTorque) or abs(cs.steeringTorque) > TORQUE_GUARD_NM:
          grip_until = timestamp + int(PRESS_COOLDOWN_S * 1e9)
    elif name == 'controllerStateBP':
      counts['mode_' + str(msg.controllerStateBP.activeLateralMode)] += 1
    elif name == 'carControl':
      cc = msg.carControl
      counts['active_control'] += int(cc.latActive)
      if ford_autocal and cs is not None and 0 <= timestamp - cs_time <= 500_000_000 and timestamp >= grip_until and cc.latActive:
        k = abs(cc.actuators.curvature)
        if math.isfinite(k) and cs.vEgo >= MIN_SPEED and k >= MIN_KAPPA and k * cs.vEgo ** 2 <= MAX_LAT_ACCEL:
          counts['ford_candidate_low' if speed_alpha(cs.vEgo) < 0.5 else 'ford_candidate_high'] += 1
  if first is None or (day is not None and local_start is None):
    return None
  return {'local_start': local_start.isoformat() if local_start else None,
          'duration_s': round((last - first) / 1e9, 2),
          'min_speed_mps': round(min_speed, 2) if math.isfinite(min_speed) else None,
          'max_speed_mps': round(max_speed, 2) if math.isfinite(max_speed) else None, **counts}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('root', type=Path, help='Local directory containing route--segment folders')
  parser.add_argument('--date', type=date.fromisoformat, help='Segment-start date, YYYY-MM-DD, in --timezone')
  parser.add_argument('--timezone', type=ZoneInfo, default=ZoneInfo('UTC'))
  parser.add_argument('--ford-autocal', action='store_true', help='Score coarse low/high-anchor curve candidates with current Ford gates')
  parser.add_argument('--route', action='append', help='Limit to this local route name; repeat for multiple routes')
  args = parser.parse_args()
  if not args.root.is_dir():
    parser.error('root must be a local directory')
  from openpilot.tools.lib.logreader import LogReader
  errors = 0
  for path in segment_logs(args.root):
    if args.route and path.parent.name.rsplit('--', 1)[0] not in args.route:
      continue
    try:
      row = summarize(LogReader(str(path), sort_by_time=True), args.timezone, args.date, args.ford_autocal)
      if row is not None:
        print(json.dumps({'segment': path.parent.name, 'log': str(path), **row}), flush=True)
    except Exception as exc:
      errors += 1
      print(f'{path}: {type(exc).__name__}: {exc}', file=sys.stderr)
  return int(errors > 0)


if __name__ == '__main__':
  sys.exit(main())
