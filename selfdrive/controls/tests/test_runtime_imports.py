"""Native Cereal imports must not evaluate capnp modules as runtime type unions."""
import importlib


def test_cereal_runtime_modules_import() -> None:
  for module in (
    "openpilot.selfdrive.selfdrived.selfdrived",
    "openpilot.selfdrive.controls.plannerd",
    "openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc",
  ):
    importlib.import_module(module)
