#!/usr/bin/env python3
"""Ensure the static gate catches real integration mistakes, without native builds."""
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
  imports = 'from openpilot.common.params import Params\nfrom openpilot.common.swaglog import cloudlog\n'
  cases = {
    'valid asynchronous write and structured logging': (
      'Params().put("BPLearnedStopsLatch", "{}", block=False)\ncloudlog.event("observation", id="test")', None),
    'removed Params API': ('Params().put_nonblocking("BPLearnedStopsLatch", "{}")', '[attr-defined]'),
    'plain info with structured keywords': ('cloudlog.info("observation", id="test")', '[call-arg]'),
    'plain warning with structured keywords': ('cloudlog.warning("evidence", failures=[])', '[call-arg]'),
    'typed IsOnroad mistaken for bytes': ('value: bytes = Params().get("IsOnroad")', '[assignment]'),
  }
  for name, (source, expected) in cases.items():
    with tempfile.TemporaryDirectory() as directory:
      probe = Path(directory) / 'probe.py'
      probe.write_text(imports + source)
      result = subprocess.run([sys.executable, '-m', 'mypy', '--config-file', 'mypy.ini', str(probe)],
                              capture_output=True, text=True)
    if expected is None:
      assert result.returncode == 0, result.stdout + result.stderr
    else:
      assert result.returncode == 1 and expected in result.stdout, result.stdout + result.stderr
    print(f'PASS: {name}')


if __name__ == '__main__':
  main()
