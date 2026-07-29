from openpilot.bluepilot.vision.vision_bsm import (
  VASM_ENABLED_REFRESH_SECONDS,
  VASM_STATE_TIMEOUT_SECONDS,
  VisionBSMCombiner,
  get_fresh_vasm_state,
)


class FakeParams:
  def __init__(self, values):
    self.values = values

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    return self.values.get(key) in ("1", b"1", True)


def test_fresh_vasm_state_is_returned():
  params = FakeParams({
    "VASMLastUpdateMonoTime": "100.0",
    "VASMLeftActive": "1",
    "VASMRightActive": "0",
  })

  assert get_fresh_vasm_state(params, now=101.0) == (True, False)


def test_stale_or_invalid_vasm_state_fails_closed():
  stale = FakeParams({"VASMLastUpdateMonoTime": "100.0", "VASMLeftActive": "1"})
  invalid = FakeParams({"VASMLastUpdateMonoTime": "invalid", "VASMLeftActive": "1"})

  assert get_fresh_vasm_state(stale, now=100.0 + VASM_STATE_TIMEOUT_SECONDS + 0.01) == (False, False)
  assert get_fresh_vasm_state(invalid, now=100.0) == (False, False)


def test_combiner_preserves_oem_and_requires_enabled_fresh_vision():
  persistent = FakeParams({"VASMEnabled": True})
  memory = FakeParams({
    "VASMLastUpdateMonoTime": "100.0",
    "VASMLeftActive": "1",
    "VASMRightActive": "0",
  })
  combiner = VisionBSMCombiner(persistent, memory)

  assert combiner.combined_state(False, True, now=101.0) == (True, True)
  persistent.values["VASMEnabled"] = False
  assert combiner.combined_state(False, True, now=101.0 + VASM_ENABLED_REFRESH_SECONDS) == (False, True)
