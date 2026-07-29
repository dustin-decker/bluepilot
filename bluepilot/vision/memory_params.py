"""Compatibility helper for process-shared, non-persistent Params."""

from __future__ import annotations

import platform

from openpilot.common.params import Params


MEMORY_PARAMS_PATH = "/dev/shm/params"


def create_memory_params(persistent_params: Params | None = None) -> Params:
  """Return the shared-memory Params store used for transient vision state."""
  if platform.system() == "Darwin":
    return persistent_params if persistent_params is not None else Params()
  return Params(MEMORY_PARAMS_PATH)
