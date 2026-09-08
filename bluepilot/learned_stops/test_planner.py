import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openpilot.common.params import Params
from .planner import PlannerStops


@pytest.mark.parametrize('mode', ['off', 'observe'])
def test_update_persists_with_native_params(tmp_path: Path, mode: str) -> None:
  params = Params(str(tmp_path))
  params.put('CurrentRoute', 'test-route', block=True)
  cp = SimpleNamespace(carFingerprint='FORD_F_150_MK14', openpilotLongitudinalControl=True)
  planner = PlannerStops(cp, params)

  class Messages(dict[str, Any]):
    services = ['carState', 'carControl', 'learnedStopsBP']
    valid = {'learnedStopsBP': True}
    logMonoTime = {'carState': 2_000_000_000, 'learnedStopsBP': 2_000_000_000}

  sm = Messages(carState=SimpleNamespace(vEgo=10, brakePressed=False, gasPressed=False, buttonEvents=[]),
                carControl=SimpleNamespace(longActive=True),
                learnedStopsBP=SimpleNamespace(data=json.dumps({'vehicle': cp.carFingerprint, 'mode': mode, 'available': True})))
  assert planner.update(sm)['apply'] is False
  # Destroying Params drains its asynchronous writer; no sleeps or real device settings.
  del planner, params
  saved_text = Params(str(tmp_path)).get('BPLearnedStopsLatch')
  assert saved_text is not None
  saved = json.loads(saved_text)
  assert saved == {'vehicle': cp.carFingerprint, 'route': 'test-route', 'latch': None, 'suppressed': []}
