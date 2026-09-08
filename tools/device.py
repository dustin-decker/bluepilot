#!/usr/bin/env python3
"""Read-only device diagnostics and guarded builds; never sync or reset source."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from cereal.messaging import SubMaster

import argparse
from collections import deque
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

ROOT = Path('/data/openpilot')
PYTHON = '/usr/local/venv/bin/python'


def require_offroad(param: bytes, started: bool, fresh: bool) -> None:
  if param != b'0' or started or not fresh:
    raise RuntimeError('Build refused: require IsOnroad=0 and fresh offroad deviceState.')


def guarded_build(sm: SubMaster) -> int:
  def check() -> None:
    sm.update(1000)
    require_offroad(Path('/data/params/d/IsOnroad').read_bytes(), sm['deviceState'].started,
                    sm.valid['deviceState'] and time.monotonic() - sm.recv_time['deviceState'] < 3)

  check()
  env = {**os.environ, 'PYTHONPATH': str(ROOT), 'PATH': '/usr/local/venv/bin:' + os.environ['PATH']}
  # Source changes are intentionally left intact. SCons only builds the current tree.
  child = subprocess.Popen(['scons', '-j4'], cwd=ROOT, env=env, start_new_session=True)
  try:
    while child.poll() is None:
      check()
    return child.returncode
  finally:
    if child.poll() is None:
      os.killpg(child.pid, signal.SIGTERM)
      try:
        child.wait(timeout=5)
      except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def remote(action: str, lines: int) -> int:
  if action == 'logs':
    paths = sorted(Path('/data/log').glob('swaglog.*'), key=lambda p: p.stat().st_mtime)
    if not paths:
      raise RuntimeError('No swaglog files found in /data/log.')
    with paths[-1].open(errors='replace') as stream:
      for line in deque(stream, maxlen=lines):
        try:
          record = json.loads(line)
          print(record.get('level', ''), record.get('ctx', {}).get('daemon', ''),
                record.get('msg$s', record.get('msg', '')), record.get('exc_info', ''))
        except ValueError:
          print(line.rstrip())
    return 0

  from cereal import messaging
  sm = messaging.SubMaster(['deviceState', 'managerState'])
  for _ in range(5):
    sm.update(1000)
    if sm.recv_frame['deviceState'] and sm.recv_frame['managerState']:
      break
  subprocess.run(['git', '-C', str(ROOT), 'log', '-1', '--format=%h %s'], check=True)
  subprocess.run(['git', '-C', str(ROOT), 'status', '--short'], check=True)
  if action == 'build':
    return guarded_build(sm)
  print('IsOnroad:', Path('/data/params/d/IsOnroad').read_text().strip())
  fresh = sm.valid['managerState'] and time.monotonic() - sm.recv_time['managerState'] < 3
  if not fresh:
    raise RuntimeError('No fresh managerState; process health unknown.')
  stopped = [p.name for p in sm['managerState'].processes if p.shouldBeRunning and not p.running]
  print('Expected processes stopped:', stopped)
  usage = os.statvfs('/data')
  print(f'Free /data: {usage.f_bavail * usage.f_frsize / 1024**3:.1f} GiB')
  return int(bool(stopped))


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=('check', 'build', 'logs'))
  parser.add_argument('--host', default=os.getenv('BP_DEVICE_HOST', 'comma@192.168.1.26'))
  parser.add_argument('--lines', type=int, default=80)
  parser.add_argument('--remote', action='store_true', help=argparse.SUPPRESS)
  args = parser.parse_args()
  if not 1 <= args.lines <= 1000:
    parser.error('--lines must be between 1 and 1000')
  if args.remote:
    try:
      return remote(args.action, args.lines)
    except (OSError, RuntimeError) as error:
      print(error, file=sys.stderr)
      return 1
  if args.host.startswith('-') or not args.host or any(c.isspace() for c in args.host):
    parser.error('invalid SSH host')
  command = shlex.join(['env', f'PYTHONPATH={ROOT}', PYTHON, '-', args.action, '--remote', '--lines', str(args.lines)])
  return subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', args.host, command],
                        input=Path(__file__).read_text(), text=True).returncode


if __name__ == '__main__':
  sys.exit(main())
