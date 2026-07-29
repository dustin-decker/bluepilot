#!/usr/bin/env python3
import hashlib
import os
import shutil
import subprocess
from contextlib import contextmanager

# NOTE: Do NOT import anything here that needs be built (e.g. params)
from openpilot.common.basedir import BASEDIR
from openpilot.common.spinner import Spinner
from openpilot.common.text_window import TextWindow
from openpilot.system.hardware import HARDWARE, AGNOS

# The venv is provisioned once (at install) and persists across OTA updates, but nothing
# reconciles it with the updated lockfile afterwards: updated.py only does a git fetch/reset.
# When an update adds a Python dependency (e.g. acados), the stale venv is missing it and
# scons dies importing it. Re-sync the venv whenever the checked-out uv.lock changes.
UV_LOCK = os.path.join(BASEDIR, "uv.lock")
PROJECT_VENV = os.path.join(BASEDIR, ".venv")
SYNC_MARKER = os.path.join(BASEDIR, ".venv", ".op_synced_lock")
DEVICE_UV_CACHE_DIR = "/data/uv-cache"


# BluePilot: prebuilt C3X installs run from /usr/local/venv rather than a
# project-local .venv. Target it when converting a prebuilt install to source.
def _active_venv() -> str | None:
  active_venv = os.getenv("VIRTUAL_ENV")
  if active_venv and os.path.isfile(os.path.join(active_venv, "bin", "python")):
    return active_venv
  return None
# End BluePilot


# BluePilot: AGNOS mounts the prebuilt environment's root filesystem read-only.
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
# End BluePilot


def _uv_lock_digest() -> str | None:
  try:
    with open(UV_LOCK, "rb") as f:
      return hashlib.sha256(f.read()).hexdigest()
  except FileNotFoundError:
    return None


def sync_python_env() -> None:
  # No-op unless uv.lock changed since the last successful sync. The marker lives inside
  # the venv, so a wiped/recreated venv also re-syncs (its marker disappears with it).
  digest = _uv_lock_digest()
  if digest is None:
    return

  # BluePilot: uv otherwise rejects an invalid/stale project .venv even though
  # the device has a valid active environment at /usr/local/venv.
  active_venv = _active_venv()
  sync_marker = os.path.join(active_venv, ".op_synced_lock") if active_venv else SYNC_MARKER

  try:
    with open(sync_marker) as f:
      if f.read().strip() == digest:
        return
  except FileNotFoundError:
    pass

  uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
  if not os.path.exists(uv):
    print("uv not found; skipping dependency sync")
    return

  # --frozen: install exactly what uv.lock pins, no re-resolution.
  # --inexact: only add missing packages, never remove extras (won't clobber a dev's env).
  sync_cmd = [uv, "sync", "--frozen", "--inexact"]
  if active_venv and os.path.realpath(active_venv) != os.path.realpath(PROJECT_VENV):
    # The prebuilt interpreter can lag .python-version while remaining compatible
    # with pyproject.toml. An explicit path prevents uv from trying to download the
    # exact .python-version on AGNOS, where managed Python downloads are disabled.
    sync_cmd.extend(["--active", "--python", os.path.join(active_venv, "bin", "python")])

  sync_env = os.environ.copy()
  if AGNOS:
    # /home is a small overlay on C3X. Large wheels such as OpenCV cannot be
    # extracted there, while /data has ample persistent storage.
    os.makedirs(DEVICE_UV_CACHE_DIR, exist_ok=True)
    sync_env["UV_CACHE_DIR"] = DEVICE_UV_CACHE_DIR

  needs_root_write = bool(AGNOS and active_venv and _venv_on_root(active_venv) and _root_readonly())
  with _writable_root(needs_root_write):
    subprocess.run(sync_cmd, cwd=BASEDIR, check=True, env=sync_env)

    os.makedirs(os.path.dirname(sync_marker), exist_ok=True)
    with open(sync_marker, "w") as f:
      f.write(digest)
  # End BluePilot


def build() -> None:
  spinner = Spinner()
  spinner.update_progress(0, 100)

  HARDWARE.set_power_save(False)
  if AGNOS:
    os.sched_setaffinity(0, range(8))  # ensure we can use the isolcpus cores

  # reconcile the venv with the checked-out lockfile before building
  try:
    sync_python_env()
  except subprocess.CalledProcessError:
    spinner.close()
    if not os.getenv("CI"):
      msg = "openpilot failed to update dependencies\n \nEnsure the device has an internet connection, then reboot."
      with TextWindow(msg) as t:
        t.wait_for_exit()
    exit(1)

  # building with all cores can result in using too much memory, so retry serially
  compile_output: list[bytes] = []
  for parallelism in ([], ["-j4"], ["-j1"]):
    compile_output.clear()
    with subprocess.Popen(["scons", *parallelism], cwd=BASEDIR, env={**os.environ, "PWD": BASEDIR}, stderr=subprocess.PIPE) as scons:
      assert scons.stderr is not None

      # Read progress from stderr and update spinner
      while scons.poll() is None:
        try:
          line = scons.stderr.readline()
          if line is None:
            continue
          line = line.rstrip()

          prefix = b'progress: '
          if line.startswith(prefix):
            progress = float(line[len(prefix):])
            spinner.update_progress(100 * min(1., progress / 100.), 100.)
          elif len(line):
            compile_output.append(line)
            print(line.decode('utf8', 'replace'))
        except Exception:
          pass

      # Drain and close the pipe before retrying or returning.
      for line in scons.stderr.read().split(b'\n'):
        line = line.rstrip()
        if len(line):
          compile_output.append(line)

    if scons.returncode == 0:
      break

  if scons.returncode != 0:
    # Build failed log errors
    error_s = b"\n".join(compile_output).decode('utf8', 'replace')

    # Show TextWindow
    spinner.close()
    if not os.getenv("CI"):
      with TextWindow("openpilot failed to build\n \n" + error_s) as t:
        t.wait_for_exit()
    exit(1)

if __name__ == "__main__":
  build()
