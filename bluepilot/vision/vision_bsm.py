"""Freshness-gated access to vision adjacent-spot detections."""

from __future__ import annotations

import time

from openpilot.common.params import Params
from openpilot.bluepilot.vision.memory_params import create_memory_params


VASM_STATE_TIMEOUT_SECONDS = 3.0
VASM_ENABLED_REFRESH_SECONDS = 2.0


def get_fresh_vasm_state(params_memory, now: float | None = None) -> tuple[bool, bool]:
  """Return V-ASM state only while the vision daemon is updating it."""
  try:
    updated_at = float(params_memory.get("VASMLastUpdateMonoTime") or 0)
  except (TypeError, ValueError):
    return False, False

  current_time = time.monotonic() if now is None else now
  age = current_time - updated_at
  if updated_at <= 0 or age < 0 or age > VASM_STATE_TIMEOUT_SECONDS:
    return False, False

  active_values = ("1", b"1", True)
  return params_memory.get("VASMLeftActive") in active_values, params_memory.get("VASMRightActive") in active_values


class VisionBSMCombiner:
  """Merge OEM blind-spot signals with fresh, enabled V-ASM detections."""

  def __init__(self, params: Params | None = None, params_memory: Params | None = None):
    self.params = params or Params()
    self.params_memory = params_memory or create_memory_params(self.params)
    self._enabled = False
    self._last_enabled_refresh = -float("inf")

  def combined_state(self, oem_left: bool, oem_right: bool, now: float | None = None) -> tuple[bool, bool]:
    current_time = time.monotonic() if now is None else now
    if current_time - self._last_enabled_refresh >= VASM_ENABLED_REFRESH_SECONDS:
      self._last_enabled_refresh = current_time
      try:
        self._enabled = self.params.get_bool("VASMEnabled")
      except Exception:
        self._enabled = False

    vision_left, vision_right = get_fresh_vasm_state(self.params_memory, current_time) if self._enabled else (False, False)
    return bool(oem_left or vision_left), bool(oem_right or vision_right)
