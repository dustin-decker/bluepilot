from pathlib import Path

from openpilot.tools.check_branch_types import annotation_failures, changed_scopes, diagnostic_fingerprint, function_scopes, relevant_errors


SOURCE = '''\
class First:
  def same(self, value: int) -> None:
    broken = value
    inherited = value

class Second:
  def same(self, value: int) -> None:
    broken = value

def outer(value: int) -> None:
  def callback(item: int) -> None:
    broken = item
  callback(value)
'''


def test_qualified_scopes_include_nested_and_class_methods() -> None:
  scopes = {scope.name for scope in function_scopes(SOURCE)}
  assert scopes == {'First.same', 'Second.same', 'outer', 'outer.callback'}
  changed = {scope.name for scope in changed_scopes(SOURCE, {3, 12})}
  assert changed == {'First.same', 'outer', 'outer.callback'}


def test_error_inside_changed_function_is_retained_even_on_unchanged_line(tmp_path: Path) -> None:
  path = tmp_path / 'sample.py'
  path.write_text(SOURCE)
  output = 'sample.py:4: error: inherited-looking diagnostic  [assignment]\n'
  relevant, unparsed = relevant_errors(output, tmp_path, {Path('sample.py'): {3}})
  assert relevant == [output.strip()]
  assert not unparsed


def test_proven_inherited_error_inside_changed_function_is_filtered(tmp_path: Path) -> None:
  path = tmp_path / 'sample.py'
  path.write_text(SOURCE)
  output = 'sample.py:4: error: inherited-looking diagnostic  [assignment]\n'
  fingerprint = diagnostic_fingerprint(output.strip(), tmp_path)
  assert fingerprint is not None
  relevant, unparsed = relevant_errors(output, tmp_path, {Path('sample.py'): {3}}, {fingerprint})
  assert not relevant
  assert not unparsed


def test_annotation_audit_checks_changed_function_bodies() -> None:
  source = '''\
def inherited(value):
  return value

def changed(value):
  return value
'''
  assert annotation_failures(source, {2}) == ['inherited: missing annotation for value, return']
  assert annotation_failures(source, {4}) == ['changed: missing annotation for value, return']


def test_unparsed_errors_fail_closed(tmp_path: Path) -> None:
  relevant, unparsed = relevant_errors('mypy.ini: error: Invalid config\n', tmp_path, {})
  assert not relevant
  assert unparsed == ['mypy.ini: error: Invalid config']


def test_error_for_untracked_path_fails_closed(tmp_path: Path) -> None:
  relevant, unparsed = relevant_errors('missing.py:1: error: Cannot find implementation  [import-not-found]\n', tmp_path, {})
  assert not relevant
  assert unparsed == ['missing.py:1: error: Cannot find implementation  [import-not-found]']


def test_openpilot_alias_is_canonicalized(tmp_path: Path) -> None:
  path = tmp_path / 'sample.py'
  path.write_text('value: int = "wrong"\n')
  relevant, unparsed = relevant_errors('openpilot/sample.py:1: error: bad  [assignment]\n', tmp_path, {Path('sample.py'): {1}})
  assert relevant == ['openpilot/sample.py:1: error: bad  [assignment]']
  assert not unparsed
