#!/usr/bin/env python3
"""Reconcile BluePilot's Python environment with the checked-out lockfile."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from contextlib import contextmanager

from openpilot.common.basedir import BASEDIR
from openpilot.system.hardware import AGNOS


UV_LOCK = os.path.join(BASEDIR, "uv.lock")
PROJECT_VENV = os.path.join(BASEDIR, ".venv")
SYNC_MARKER = os.path.join(PROJECT_VENV, ".op_synced_lock")
DEVICE_UV_CACHE_DIR = "/data/uv-cache"


def _active_venv() -> str | None:
  """Return the active environment when it contains a usable interpreter."""
  active_venv = os.getenv("VIRTUAL_ENV")
  if active_venv and os.path.isfile(os.path.join(active_venv, "bin", "python")):
    return active_venv
  return None


def _root_readonly() -> bool:
  return bool(os.statvfs("/").f_flag & os.ST_RDONLY)


def _venv_on_root(venv: str) -> bool:
  return os.path.realpath(venv).startswith("/usr/local/")


@contextmanager
def _writable_root(enabled: bool):
  remounted = False
  if enabled:
    subprocess.run(["sudo", "mount", "-o", "remount,rw", "/"], check=True)
    remounted = True
  try:
    yield
  finally:
    if remounted:
      subprocess.run(["sudo", "mount", "-o", "remount,ro", "/"], check=True)


def _uv_lock_digest() -> str | None:
  try:
    with open(UV_LOCK, "rb") as f:
      return hashlib.sha256(f.read()).hexdigest()
  except FileNotFoundError:
    return None


def sync_python_env() -> None:
  """Install locked dependencies when the active environment is stale."""
  digest = _uv_lock_digest()
  if digest is None:
    return

  # Prebuilt C3X installs run from /usr/local/venv, not the project .venv.
  active_venv = _active_venv()
  root_active_venv = bool(AGNOS and active_venv and _venv_on_root(active_venv))
  sync_marker = os.path.join(DEVICE_UV_CACHE_DIR, ".op_synced_lock") if root_active_venv else (
    os.path.join(active_venv, ".op_synced_lock") if active_venv else SYNC_MARKER
  )

  # Recovery builds predating the /data marker wrote it into /usr/local/venv.
  marker_paths = [sync_marker]
  if root_active_venv:
    marker_paths.append(os.path.join(active_venv, ".op_synced_lock"))
  for marker_path in marker_paths:
    try:
      with open(marker_path) as f:
        if f.read().strip() == digest:
          return
    except FileNotFoundError:
      pass

  uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
  if not os.path.exists(uv):
    print("uv not found; skipping dependency sync")
    return

  sync_cmd = [uv, "sync", "--frozen", "--inexact"]
  if active_venv and os.path.realpath(active_venv) != os.path.realpath(PROJECT_VENV):
    # Avoid trying to download the exact .python-version on AGNOS.
    sync_cmd.extend(["--active", "--python", os.path.join(active_venv, "bin", "python")])

  sync_env = os.environ.copy()
  if AGNOS:
    # /home is a small overlay; large wheels such as OpenCV need /data.
    os.makedirs(DEVICE_UV_CACHE_DIR, exist_ok=True)
    sync_env["UV_CACHE_DIR"] = DEVICE_UV_CACHE_DIR

  needs_root_write = bool(root_active_venv and _root_readonly())
  run_cmd = ([
    "sudo", "env",
    f"UV_CACHE_DIR={DEVICE_UV_CACHE_DIR}",
    f"VIRTUAL_ENV={active_venv}",
    *sync_cmd,
  ] if root_active_venv else sync_cmd)

  with _writable_root(needs_root_write):
    # /usr/local/venv is root-owned as well as mounted read-only.
    subprocess.run(run_cmd, cwd=BASEDIR, check=True, env=sync_env)

    os.makedirs(os.path.dirname(sync_marker), exist_ok=True)
    with open(sync_marker, "w") as f:
      f.write(digest)
