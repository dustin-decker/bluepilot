from types import SimpleNamespace

from openpilot.system.manager.process_config import managed_processes


class FakeParams:
  def __init__(self, enabled):
    self.enabled = enabled

  def get_bool(self, key):
    assert key == "VASMEnabled"
    return self.enabled


def test_vasm_process_defaults_off_and_runs_only_onroad():
  process = managed_processes["adj_spot_monitor_vision"]

  assert not process.should_run(False, FakeParams(False), SimpleNamespace())
  assert not process.should_run(False, FakeParams(True), SimpleNamespace())
  assert not process.should_run(True, FakeParams(False), SimpleNamespace())
  assert process.should_run(True, FakeParams(True), SimpleNamespace())
