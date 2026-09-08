#!/usr/bin/env python3
"""Replay the real card process offline with Ford's additional logged inputs."""

from typing import Any
from collections.abc import Iterable
import argparse
import copy
import json
import multiprocessing
from pathlib import Path

import numpy as np
from openpilot.common.params import Params
from openpilot.common.params_pyx import CPP_2_PYTHON
from openpilot.selfdrive.test.process_replay.process_replay import get_process_config, replay_process
from openpilot.tools.lib.logreader import LogReader
from openpilot.tools.lib.source_provenance import source_provenance


def angles(messages: Iterable[Any]) -> np.ndarray:
  result = []
  for m in messages:
    if m.which() == 'sendcan':
      for c in m.sendcan:
        if c.address == 982 and c.src == 0:
          b = bytes(c.dat)
          result.append([m.logMonoTime * 1e-9, (((b[3] & 31) << 6) | (b[4] >> 2)) * 0.0005 - 0.5, (b[0] >> 4) & 7])
  return np.asarray(result).reshape(-1, 3)


def main() -> None:
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('logs', nargs='+')
  p.add_argument('--output', required=True, type=Path)
  p.add_argument('--revision', help='Source revision when running an archived checkout without Git metadata')
  a = p.parse_args()
  source = source_provenance(a.revision)
  if not Path('sunnypilot/neural_network_data/neural_network_lateral_control').is_dir():
    p.error('Missing neural-network-data submodule; run mise run tooling:setup')
  cfg = copy.deepcopy(get_process_config('card'))
  cfg.pubs += ['carControlSP', 'longitudinalPlanSP', 'modelV2', 'liveParameters', 'selfdriveState', 'radarState', 'liveDelay']
  messages = sorted((m for log in a.logs for m in LogReader(log)), key=lambda m: m.logMonoTime)
  initial = next(m.initData for m in messages if m.which() == 'initData')
  keys = set(Params().all_keys())
  params = {
    e.key: CPP_2_PYTHON[Params().get_type(e.key)](bytes(e.value)) for e in initial.params.entries if e.value and (e.key.encode() in keys or e.key in keys)
  }
  captures: dict[str, dict[str, str]] = {}
  a.output.mkdir(parents=True, exist_ok=True)
  try:
    replayed = replay_process(cfg, messages, custom_params=params, captured_output_store=captures, disable_progress=True)
  finally:
    # Process text can contain private route/vehicle identifiers: keep output in ignored analysis directories.
    (a.output / 'process-output.json').write_text(json.dumps(captures, indent=2))
  recorded, actual = angles(messages), angles(replayed)
  np.savez_compressed(a.output / 'wire-output.npz', recorded=recorded, replayed=actual)
  stats = {
    'recorded_commit': initial.gitCommit,
    'replay_commit': source['commit'] or a.revision,
    'source_provenance': source,
    'recorded_frames': len(recorded),
    'replayed_frames': len(actual),
    'params_restored': len(params),
    'warmup_excluded_s': 5,
  }
  if len(actual) and len(recorded) >= 2:
    i = np.searchsorted(recorded[:, 0], actual[:, 0]).clip(1, len(recorded) - 1)
    i -= np.abs(recorded[i - 1, 0] - actual[:, 0]) < np.abs(recorded[i, 0] - actual[:, 0])
    matched = (actual[:, 0] > recorded[0, 0] + 5) & (abs(recorded[i, 0] - actual[:, 0]) < 0.03) & (actual[:, 2] != 0) & (recorded[i, 2] != 0)
    stats['active_matched_frames'] = int(matched.sum())
    if matched.any():
      stats['angle_error_rad_p50_p95_p99_max'] = np.percentile(abs(recorded[i[matched], 1] - actual[matched, 1]), [50, 95, 99, 100]).tolist()
  (a.output / 'result.json').write_text(json.dumps(stats, indent=2))
  print(json.dumps(stats, indent=2))


if __name__ == '__main__':
  multiprocessing.set_start_method('fork')
  main()
