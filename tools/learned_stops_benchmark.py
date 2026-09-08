#!/usr/bin/env python3
"""Compare recorder CPU on identical route input and isolated copies of a stop DB.

Exercises each real daemon loop, native Params and SQLite; replaces only live
message transport with recorded inputs/collected outputs. Excludes log loading
and IPC wait/transport overhead. Never launches vehicle-control processes.
"""
from __future__ import annotations

import argparse
import cProfile
import importlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
import uuid
import itertools
import multiprocessing
import shutil
from types import ModuleType
from typing import Any
from unittest.mock import patch  # noqa: TID251 -- isolated native benchmark patching
from contextlib import ExitStack
from collections.abc import Iterator

from cereal import messaging
from openpilot.common.params import Params
from openpilot.tools.lib.logreader import LogReader

SERVICES = {'carState', 'carControl', 'radarState', 'modelV2', 'liveLocationKalman',
            'gpsLocation', 'gpsLocationExternal', 'roadEncodeIdx', 'wideRoadEncodeIdx'}


def require_offroad() -> None:
  if Path('/AGNOS').exists() and Path('/data/params/d/IsOnroad').read_bytes() != b'0':
    raise RuntimeError('Stop benchmark: vehicle is not offroad')


class EndReplay(Exception):
  pass


class ReplayMessages:
  def __init__(self, events: list[Any], services: list[str]) -> None:
    self.events = iter(events)
    self.last_guard = 0.0
    self.services = services
    self.updated: dict[str, bool] = {}
    self.valid = dict.fromkeys(services, False)
    self.logMonoTime = dict.fromkeys(services, 0)
    self.data: dict[str, Any] = {}

  def __getitem__(self, name: str) -> Any:
    return self.data[name]

  def update(self, timeout: int = 100) -> None:
    now = time.monotonic()
    if now - self.last_guard >= .5:
      require_offroad()
      self.last_guard = now
    self.updated = dict.fromkeys(self.services, False)
    for event in self.events:
      kind = event.which()
      if kind not in self.services:
        continue
      self.data[kind] = getattr(event, kind)
      self.updated[kind] = True
      self.valid[kind] = event.valid
      self.logMonoTime[kind] = event.logMonoTime
      if kind == 'carState':
        return
    raise EndReplay


def measure(module: ModuleType, events: list[Any], cp: bytes, route: str, database: Path) -> dict[str, Any]:
  outputs: list[dict[str, Any]] = []

  class Publisher:
    def __init__(self, services: list[str]) -> None:
      pass

    def send(self, name: str, message: Any) -> None:
      outputs.append({'valid': message.valid, 'data': json.loads(message.learnedStopsBP.data)})

  with tempfile.TemporaryDirectory(prefix='stops-benchmark-') as directory:
    root = Path(directory)
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as source, sqlite3.connect(root / 'stops.sqlite3') as dest:
      source.backup(dest)
      dest.execute("INSERT OR REPLACE INTO settings VALUES ('mode','observe')")
      dest.execute("INSERT OR REPLACE INTO settings VALUES ('drive',?)", (json.dumps({'id': 'benchmark', 'seen': time.time()}),))  # noqa: TID251 -- persisted wall clock
    params = Params(str(root / 'params'))
    params.put('CarParams', cp, block=True)
    params.put('CurrentRoute', route, block=True)
    params.put_bool('UbloxAvailable', any(e.which() == 'gpsLocationExternal' for e in events), block=True)

    def subscriber(services: list[str], **kwargs: Any) -> ReplayMessages:
      return ReplayMessages(events, services)

    def batches(services: list[str]) -> Iterator[list[Any]]:
      last_guard = 0.0
      for event in events:
        now = time.monotonic()
        if now - last_guard >= .5:
          require_offroad()
          last_guard = now
        if event.which() in services:
          yield [event]

    identifiers = itertools.count(1)
    with ExitStack() as patches, patch('uuid.uuid4', side_effect=lambda: uuid.UUID(int=next(identifiers))), \
         patch.dict(os.environ, {'BP_LEARNED_STOPS_ROOT': str(root)}), \
         patch('openpilot.common.params.Params', return_value=params), \
         patch.object(messaging, 'SubMaster', subscriber), patch.object(messaging, 'PubMaster', Publisher):
      if hasattr(module, 'message_batches'):
        patches.enter_context(patch.object(module, 'message_batches', batches))
      start = time.process_time()
      try:
        module.main()
      except EndReplay:
        pass
      elapsed = time.process_time() - start
    with sqlite3.connect(root / 'stops.sqlite3') as db:
      observations = [json.loads(row[0]) for row in db.execute("SELECT data FROM observations WHERE drive='benchmark' ORDER BY utc")]
    # IDs vary per run. Only semantically relevant recorder evidence is compared.
    for observation in observations:
      observation.pop('id', None)
    return {'cpu_seconds': elapsed, 'publications': outputs, 'observations': observations}


def native_worker(package: str, root: str, cp: bytes, route: str, external_gps: bool, ready: Any, profile: str | None) -> None:
  """Only a recorder, in a private IPC namespace; never a control process."""
  params = Params(str(Path(root) / 'params'))
  params.put('CarParams', cp, block=True)
  params.put('CurrentRoute', route, block=True)
  params.put_bool('UbloxAvailable', external_gps, block=True)
  module = importlib.import_module(package)
  with ExitStack() as patches:
    patches.enter_context(patch('openpilot.common.params.Params', return_value=params))
    if hasattr(module, 'message_batches'):
      original_batches = module.message_batches

      def batches(services: list[str]) -> Any:
        first = True
        for batch in original_batches(services):
          if first:
            ready.send(True)
            first = False
          yield batch

      patches.enter_context(patch.object(module, 'message_batches', batches))
    else:
      original = messaging.SubMaster

      def subscriber(*args: Any, **kwargs: Any) -> Any:
        sm = original(*args, **kwargs)
        ready.send(True)
        return sm

      patches.enter_context(patch.object(messaging, 'SubMaster', subscriber))
    if profile:
      import signal
      profiler = cProfile.Profile()
      def save(signum: int, frame: Any) -> None:
        profiler.disable()
        profiler.dump_stats(profile)
        raise SystemExit(0)
      signal.signal(signal.SIGTERM, save)
      profiler.runcall(module.main)
    else:
      module.main()


def measure_native(module: ModuleType, events: list[Any], cp: bytes, route: str, database: Path, profile: Path | None = None) -> dict[str, Any]:
  """Real-time playback through native IPC; measure child CPU, not publisher CPU."""
  import psutil
  require_offroad()
  if not sys.platform.startswith('linux'):
    raise RuntimeError('Native transport measurement requires Linux msgq')
  # Serialize before timing, and never use any real driving service namespace.
  wire = [(event.logMonoTime / 1e9, event.which(), event.as_builder().to_bytes()) for event in events]
  with tempfile.TemporaryDirectory(prefix='stops-native-') as directory:
    root = Path(directory)
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as source, sqlite3.connect(root / 'stops.sqlite3') as dest:
      source.backup(dest)
      dest.execute("INSERT OR REPLACE INTO settings VALUES ('mode','observe')")
    prefix = 'benchmark_' + uuid.uuid4().hex
    ipc = Path('/dev/shm') / ('msgq_' + prefix)
    ipc.mkdir()
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=native_worker, args=(module.__name__, directory, cp, route,
                            any(name == 'gpsLocationExternal' for _, name, _ in wire), sender, str(profile) if profile else None))
    publisher: Any = None
    subscriber: Any = None
    try:
      with patch.dict(os.environ, {'OPENPILOT_PREFIX': prefix, 'BP_LEARNED_STOPS_ROOT': directory, 'ZMQ': '0'}):
        publisher = messaging.PubMaster(sorted({name for _, name, _ in wire}))
        subscriber = messaging.SubMaster(['learnedStopsBP'])
        child.start()
        if not receiver.poll(20) or receiver.recv() is not True:
          raise RuntimeError('Recorder did not start')
        process = psutil.Process(child.pid)
        before = process.cpu_times()
        start = time.monotonic()
        last_guard = start
        publications = 0
        for stamp, name, payload in wire:
          now = time.monotonic()
          if now - last_guard >= .5:
            require_offroad()
            if not child.is_alive():
              raise RuntimeError('Recorder crashed during native replay')
            last_guard = now
          wait = start + stamp - wire[0][0] - now
          if wait > 0:
            time.sleep(wait)
          publisher.send(name, payload)
          subscriber.update(0)
          publications += bool(subscriber.updated['learnedStopsBP'])
        time.sleep(.2)
        after = process.cpu_times()
        memory = process.memory_full_info()
        elapsed = time.monotonic() - start
        cpu = after.user + after.system - before.user - before.system
        return {'cpu_seconds': cpu, 'wall_seconds': elapsed, 'core_percent': 100 * cpu / elapsed,
                'rss_mib': memory.rss / 2**20, 'pss_mib': memory.pss / 2**20, 'publications': publications}
    finally:
      if child.pid is not None:
        child.terminate()
        child.join(5)
        if child.is_alive():
          child.kill()
          child.join()
      receiver.close()
      sender.close()
      del publisher, subscriber
      shutil.rmtree(ipc)


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--baseline-package', required=True, help='Importable archived learned_stops package, e.g. baseline_stops')
  parser.add_argument('--candidate-package', default='openpilot.bluepilot.learned_stops')
  parser.add_argument('--database', type=Path, required=True)
  parser.add_argument('--logs', type=Path, nargs='+', required=True)
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--native-transport', action='store_true')
  parser.add_argument('--profile-candidate', type=Path)
  parser.add_argument('--repeats', type=int, default=3)
  args = parser.parse_args()
  if not 1 <= args.repeats <= 10:
    parser.error('repeats must be between 1 and 10')
  require_offroad()
  events: list[Any] = []
  cp = None
  for path in args.logs:
    for event in LogReader(str(path), sort_by_time=True):
      if event.which() == 'carParams':
        cp = event.carParams.as_builder().to_bytes()
      elif event.which() in SERVICES:
        events.append(event)
  if cp is None or not events:
    parser.error('logs must contain CarParams and recorder inputs')
  events.sort(key=lambda e: e.logMonoTime)
  route = args.logs[0].parent.name.rsplit('--', 1)[0]
  modules = {name: importlib.import_module(package + '.daemon')
             for name, package in [('baseline', args.baseline_package), ('candidate', args.candidate_package)]}
  if args.native_transport:
    native: dict[str, list[dict[str, Any]]] = {'baseline': [], 'candidate': []}
    for repeat in range(args.repeats):
      for name in (['baseline', 'candidate'] if repeat % 2 == 0 else ['candidate', 'baseline']):
        native[name].append(measure_native(modules[name], events, cp, route, args.database, args.profile_candidate if name == 'candidate' else None))
        print(name, native[name][-1], flush=True)
    median = {name: statistics.median(row['cpu_seconds'] for row in rows) for name, rows in native.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'scope': 'Native recorder process with real-time private IPC replay',
                                     'runs': native, 'speedup': median['baseline'] / median['candidate']}, indent=2))
    return
  times: dict[str, list[float]] = {'baseline': [], 'candidate': []}
  reference = None
  for repeat in range(args.repeats):
    for name in (['baseline', 'candidate'] if repeat % 2 == 0 else ['candidate', 'baseline']):
      if name == 'candidate' and args.profile_candidate:
        profiler = cProfile.Profile()
        result = profiler.runcall(measure, modules[name], events, cp, route, args.database)
        profiler.dump_stats(str(args.profile_candidate))
      else:
        result = measure(modules[name], events, cp, route, args.database)
      times[name].append(result.pop('cpu_seconds'))
      if reference is None:
        reference = result
      if result != reference:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix('.mismatch.json').write_text(json.dumps({'reference': reference, name: result}, indent=2))
        raise AssertionError('Recorder output changed; inspect mismatch artifact before accepting performance results')
      print(f'{name}: {times[name][-1]:.3f}s CPU', flush=True)
  seconds = (events[-1].logMonoTime - events[0].logMonoTime) / 1e9
  medians = {name: statistics.median(values) for name, values in times.items()}
  report = {'scope': __doc__, 'route_seconds': seconds, 'cpu_seconds': times, 'median_cpu_seconds': medians,
            'speedup': medians['baseline'] / medians['candidate'],
            'core_percent_equivalent': {name: value / seconds * 100 for name, value in medians.items()},
            'publications': len(reference['publications']) if reference else 0,
            'new_observations': len(reference['observations']) if reference else 0, 'outputs_equal': True}
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps(report, indent=2) + '\n')
  print(json.dumps(report, indent=2))


if __name__ == '__main__':
  main()
