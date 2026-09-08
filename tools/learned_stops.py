#!/usr/bin/env python3
"""Replay stop observations into an isolated evidence store; never change live modes."""
import argparse
from collections import Counter
import json
from pathlib import Path

from openpilot.bluepilot.learned_stops.core import Observer
from openpilot.bluepilot.learned_stops.store import Store
from openpilot.bluepilot.learned_stops.telemetry import Telemetry


def replay(logs: Path, output: Path) -> None:
  from openpilot.tools.lib.logreader import LogReader

  store = Store(output)
  files = sorted(logs.glob('*/rlog.zst'), key=lambda p: (*p.parent.name.rsplit('--', 1)[:1], int(p.parent.name.rsplit('--', 1)[1])))
  if not files:
    files = sorted(logs.glob('*/qlog.zst'), key=lambda p: (*p.parent.name.rsplit('--', 1)[:1], int(p.parent.name.rsplit('--', 1)[1])))
  if not files:
    raise ValueError('No segment rlog.zst or qlog.zst files found')
  route, last_utc, drive = None, None, None
  observer, telemetry, vehicle = None, None, None
  counts: Counter[str] = Counter()
  for number, path in enumerate(files):
    current = path.parent.name.rsplit('--', 1)[0]
    if current != route:
      route, observer, telemetry, vehicle = current, None, Telemetry(), None
    assert telemetry is not None and route is not None
    for event in LogReader(str(path), sort_by_time=True):
      kind = event.which()
      if kind == 'carParams':
        vehicle = event.carParams.carFingerprint
      if kind not in ('carState', 'carControl', 'radarState', 'modelV2', 'liveLocationKalman',
                      'gpsLocation', 'gpsLocationExternal', 'roadEncodeIdx', 'wideRoadEncodeIdx'):
        continue
      if kind in ('gpsLocation', 'gpsLocationExternal') and event.valid:
        telemetry.gps_service = kind
      telemetry.feed(event)
      if kind != 'carState':
        continue
      result = telemetry.sample(path.parent.name)
      if not result or result[0].utc <= 0 or vehicle is None:
        continue
      sample = result[0]
      if observer is None:
        if last_utc is None or not 0 <= sample.utc - last_utc < 1200:
          drive = route
        assert drive is not None
        observer = Observer(drive)
      last_utc = sample.utc
      counts['samples'] += 1
      obs = observer.update(sample)
      if obs:
        telemetry.evidence(obs, route)
        store.add(vehicle, obs)
        counts['observations'] += 1
        counts.update(obs['reasons'])
    if number % 20 == 0:
      print(f'{number + 1}/{len(files)} segments; {counts["observations"]} observations', flush=True)
  stops = store.list()
  summary = {'input': str(logs), 'segments': len(files), 'routes': len({p.parent.name.rsplit('--', 1)[0] for p in files}),
             'log_kind': files[0].name, 'counts': dict(counts), 'locations': len(stops),
             'qualified': sum(s['ready'] for s in stops), 'limits': 'Retrospective observation replay, not stopping-control or physical-accuracy validation.'}
  (output / 'replay-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
  (output / 'observations.json').write_text(json.dumps({'version': 1, 'stops': stops}, indent=2) + '\n')
  print(json.dumps(summary, indent=2))


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest='command', required=True)
  replay_parser = sub.add_parser('replay')
  replay_parser.add_argument('--logs', type=Path, required=True)
  replay_parser.add_argument('--output', type=Path, required=True)
  osm_parser = sub.add_parser('osm')
  osm_parser.add_argument('--bounds', type=float, nargs=4, required=True, metavar=('SOUTH', 'WEST', 'NORTH', 'EAST'))
  osm_parser.add_argument('--output', type=Path, required=True)
  simulation = sub.add_parser('simulate')
  simulation.add_argument('--output', type=Path, required=True)
  frames = sub.add_parser('capture')
  frames.add_argument('--store', type=Path, required=True)
  frames.add_argument('--logs', type=Path, required=True)
  args = parser.parse_args()
  if args.command == 'replay':
    replay(args.logs, args.output)
  elif args.command == 'osm':
    from openpilot.bluepilot.learned_stops.osm import fetch
    fetch(args.bounds, args.output)
  elif args.command == 'capture':
    from openpilot.bluepilot.learned_stops.evidence import collect
    from openpilot.common.params import Params
    failures = collect(Store(args.store), args.logs, lambda: Params().get_bool('IsOnroad'))
    print(json.dumps(failures, indent=2))
    if failures:
      raise SystemExit(1)
  else:
    from openpilot.bluepilot.learned_stops.test_mpc import simulate
    results = [simulate(speed, delay) for speed in (10, 25, 45, 65, 70) for delay in (.1, .3, .6)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + '\n')
    print('Saved diagnostic simulations. Solver failures and stopping error must be reviewed; this does not validate Control.')


if __name__ == '__main__':
  main()
