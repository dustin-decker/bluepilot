from openpilot.bluepilot.vision import memory_params


class FakeParams:
  def __init__(self, path=""):
    self.path = path


def test_linux_memory_params_use_shared_memory(monkeypatch):
  monkeypatch.setattr(memory_params.platform, "system", lambda: "Linux")
  monkeypatch.setattr(memory_params, "Params", FakeParams)

  params = memory_params.create_memory_params(FakeParams("/persistent"))

  assert params.path == memory_params.MEMORY_PARAMS_PATH


def test_darwin_memory_params_fall_back_to_persistent_store(monkeypatch):
  persistent = FakeParams("/persistent")
  monkeypatch.setattr(memory_params.platform, "system", lambda: "Darwin")
  monkeypatch.setattr(memory_params, "Params", FakeParams)

  assert memory_params.create_memory_params(persistent) is persistent
