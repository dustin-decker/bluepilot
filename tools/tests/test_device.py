from types import SimpleNamespace
from typing import Any, cast

import pytest

from openpilot.tools import device


@pytest.mark.parametrize('param,started,fresh', [(b'1', True, True), (b'', False, True), (b'0', False, False),
                                               (b'0', True, True), (b'false', False, True)])
def test_build_fails_closed(param: bytes, started: bool, fresh: bool) -> None:
  with pytest.raises(RuntimeError):
    device.require_offroad(param, started, fresh)


def test_confirmed_offroad() -> None:
  device.require_offroad(b'0', False, True)


def test_build_stops_on_ignition_and_preserves_tree(monkeypatch: pytest.MonkeyPatch) -> None:
  calls: list[Any] = []
  monkeypatch.setattr(device.Path, 'read_bytes', lambda _: b'0')
  monkeypatch.setattr(device.time, 'monotonic', lambda: 10)

  class State:
    valid = {'deviceState': True}
    recv_time = {'deviceState': 10}
    updates = 0

    def update(self, _timeout: int) -> None:
      self.updates += 1

    def __getitem__(self, _service: str) -> SimpleNamespace:
      return SimpleNamespace(started=self.updates > 1)

  class Child:
    pid = 123

    def poll(self) -> None:
      return None

    def wait(self, timeout: float | None) -> None:
      calls.append(('wait', timeout))

  def spawn(argv: list[str], **kwargs: Any) -> Child:
    calls.append(argv)
    assert kwargs['start_new_session']
    return Child()

  monkeypatch.setattr(device.subprocess, 'Popen', spawn)
  monkeypatch.setattr(device.os, 'killpg', lambda pid, sig: calls.append(('kill', pid, sig)))
  with pytest.raises(RuntimeError):
    device.guarded_build(cast(Any, State()))
  assert calls == [['scons', '-j4'], ('kill', 123, device.signal.SIGTERM), ('wait', 5)]
