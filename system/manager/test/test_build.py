import hashlib

import pytest

import openpilot.system.manager.build as build

DIGEST_V1 = hashlib.sha256(b"lock-v1").hexdigest()


class TestSyncPythonEnv:
  """
  Regression tests for the venv/lockfile reconciliation in build.py.

  The venv is provisioned once at install and persists across OTA updates, but nothing
  re-syncs it afterwards. When an update adds a Python dependency (e.g. acados), the stale
  venv is missing it and scons dies importing it. sync_python_env() re-runs `uv sync`
  whenever the checked-out uv.lock differs from what the venv was last synced against.
  """

  def _run(self, mocker, tmp_path, lock_bytes=b"lock-v1", marker_text=None, uv_found=True,
           active_venv=None, agnos=False, root_readonly=False, venv_on_root=False,
           legacy_marker_text=None):
    lock = tmp_path / "uv.lock"
    if lock_bytes is not None:
      lock.write_bytes(lock_bytes)
    project_venv = tmp_path / ".venv"
    device_cache = tmp_path / "uv-cache"
    root_active_venv = bool(agnos and active_venv and venv_on_root)
    marker = (device_cache if root_active_venv else (active_venv or project_venv)) / ".op_synced_lock"
    if marker_text is not None:
      marker.parent.mkdir(parents=True, exist_ok=True)
      marker.write_text(marker_text)
    if legacy_marker_text is not None:
      legacy_marker = active_venv / ".op_synced_lock"
      legacy_marker.parent.mkdir(parents=True, exist_ok=True)
      legacy_marker.write_text(legacy_marker_text)

    calls: list[list[str]] = []
    call_kwargs: list[dict] = []
    mocker.patch.multiple(build, UV_LOCK=str(lock), PROJECT_VENV=str(project_venv),
                          SYNC_MARKER=str(project_venv / ".op_synced_lock"),
                          DEVICE_UV_CACHE_DIR=str(device_cache), AGNOS=agnos)
    mocker.patch.object(build, "_active_venv", return_value=str(active_venv) if active_venv else None)
    mocker.patch.object(build, "_root_readonly", return_value=root_readonly)
    mocker.patch.object(build, "_venv_on_root", return_value=venv_on_root)
    mocker.patch.object(build.shutil, "which", return_value="/usr/bin/uv" if uv_found else None)
    mocker.patch.object(build.os.path, "exists", return_value=uv_found)
    def capture_run(cmd, **kwargs):
      calls.append(cmd)
      call_kwargs.append(kwargs)
    mocker.patch.object(build.subprocess, "run", side_effect=capture_run)

    build.sync_python_env()
    return marker, calls, call_kwargs

  def test_first_run_syncs_and_records_marker(self, mocker, tmp_path):
    marker, calls, _ = self._run(mocker, tmp_path)
    assert len(calls) == 1
    assert "sync" in calls[0] and "--frozen" in calls[0]
    assert marker.read_text().strip() == DIGEST_V1

  def test_unchanged_lock_is_noop(self, mocker, tmp_path):
    _, calls, _ = self._run(mocker, tmp_path, marker_text=DIGEST_V1)
    assert calls == []

  def test_changed_lock_triggers_resync(self, mocker, tmp_path):
    marker, calls, _ = self._run(mocker, tmp_path, marker_text=hashlib.sha256(b"OLD").hexdigest())
    assert len(calls) == 1
    assert marker.read_text().strip() == DIGEST_V1

  def test_prebuilt_device_syncs_active_venv(self, mocker, tmp_path):
    marker, calls, _ = self._run(mocker, tmp_path, active_venv=tmp_path / "active-venv")

    assert calls == [[
      "/usr/bin/uv", "sync", "--frozen", "--inexact", "--active",
      "--python", str(tmp_path / "active-venv" / "bin" / "python"),
    ]]
    assert marker.read_text().strip() == DIGEST_V1

  def test_agnos_sync_uses_data_cache(self, mocker, tmp_path):
    _, _, call_kwargs = self._run(mocker, tmp_path, agnos=True)

    assert call_kwargs[0]["env"]["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")
    assert (tmp_path / "uv-cache").is_dir()

  def test_readonly_agnos_venv_remounts_root_for_sync(self, mocker, tmp_path):
    marker, calls, _ = self._run(
      mocker, tmp_path,
      active_venv=tmp_path / "active-venv",
      agnos=True,
      root_readonly=True,
      venv_on_root=True,
    )

    assert calls[0] == ["sudo", "mount", "-o", "remount,rw", "/"]
    assert calls[1][:5] == [
      "sudo", "env",
      f"UV_CACHE_DIR={tmp_path / 'uv-cache'}",
      f"VIRTUAL_ENV={tmp_path / 'active-venv'}",
      "/usr/bin/uv",
    ]
    assert "sync" in calls[1]
    assert calls[2] == ["sudo", "mount", "-o", "remount,ro", "/"]
    assert marker.read_text().strip() == DIGEST_V1

  def test_legacy_root_venv_marker_avoids_resync(self, mocker, tmp_path):
    _, calls, _ = self._run(
      mocker, tmp_path,
      active_venv=tmp_path / "active-venv",
      agnos=True,
      root_readonly=True,
      venv_on_root=True,
      legacy_marker_text=DIGEST_V1,
    )

    assert calls == []

  def test_writable_root_restores_readonly_after_failure(self, mocker):
    run = mocker.patch.object(build.subprocess, "run")

    with pytest.raises(RuntimeError), build._writable_root(True):
      raise RuntimeError("sync failed")

    assert run.call_args_list == [
      mocker.call(["sudo", "mount", "-o", "remount,rw", "/"], check=True),
      mocker.call(["sudo", "mount", "-o", "remount,ro", "/"], check=True),
    ]

  def test_missing_lockfile_is_noop(self, mocker, tmp_path):
    _, calls, _ = self._run(mocker, tmp_path, lock_bytes=None)
    assert calls == []

  def test_missing_uv_binary_is_noop(self, mocker, tmp_path):
    _, calls, _ = self._run(mocker, tmp_path, uv_found=False)
    assert calls == []
