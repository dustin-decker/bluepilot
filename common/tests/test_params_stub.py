"""Prevent the checked-in native API stub from drifting from the loaded binding."""
import ast
import inspect
from pathlib import Path

from openpilot.common.params import Params


def test_params_stub_matches_native_signatures() -> None:
  stub = ast.parse((Path(__file__).parents[1] / 'params_pyx.pyi').read_text())
  cls = next(node for node in stub.body if isinstance(node, ast.ClassDef) and node.name == 'Params')
  for method in cls.body:
    if not isinstance(method, ast.FunctionDef) or method.name == '__init__':
      continue
    native = inspect.signature(getattr(Params, method.name))
    assert list(native.parameters) == [arg.arg for arg in method.args.args], method.name
    required = sum(p.default is inspect.Parameter.empty for p in native.parameters.values())
    assert required == len(method.args.args) - len(method.args.defaults), method.name
