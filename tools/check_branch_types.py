#!/usr/bin/env python3
"""Type-check Python changed from bp-dev-models without inheriting its backlog."""
import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


MYPY_VERSION = '2.3.1'
ERROR = re.compile(r'^(?P<path>.+?):(?P<line>\d+)(?::\d+)?: error:')
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(?P<line>\d+)(?:,(?P<count>\d+))? @@')


@dataclass(frozen=True)
class FunctionScope:
  name: str
  start: int
  end: int
  header_end: int
  node: ast.FunctionDef | ast.AsyncFunctionDef


def run(*args: str, root: Path) -> str:
  result = subprocess.run(['git', *args], cwd=root, text=True, capture_output=True)
  if result.returncode:
    raise RuntimeError(result.stderr.strip() or 'git command failed')
  return result.stdout


def base_commit(root: Path) -> str:
  for ref in ('origin/bp-dev-models', 'bp-dev-models'):
    result = subprocess.run(['git', 'merge-base', ref, 'HEAD'], cwd=root, text=True, capture_output=True)
    if result.returncode == 0:
      return result.stdout.strip()
  raise RuntimeError('Cannot find bp-dev-models. CI must check out with fetch-depth: 0 and fetch origin/bp-dev-models.')


def changed_python_files(base: str, root: Path) -> list[Path]:
  names = run('diff', '--name-only', '--diff-filter=ACMR', base, '--', root.as_posix(), root=root)
  untracked = run('ls-files', '--others', '--exclude-standard', '--', root.as_posix(), root=root)
  return sorted({Path(name) for name in [*names.splitlines(), *untracked.splitlines()] if name.endswith('.py')})


def added_lines(diff: str) -> set[int]:
  lines: set[int] = set()
  current = 0
  for line in diff.splitlines():
    match = HUNK.match(line)
    if match:
      current = int(match['line'])
    elif line.startswith('+') and not line.startswith('+++'):
      lines.add(current)
      current += 1
    elif not line.startswith('-'):
      current += 1
  return lines


def file_changes(base: str, path: Path, root: Path) -> set[int]:
  tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', '--', path.as_posix()], cwd=root, capture_output=True)
  if tracked.returncode:
    return set(range(1, len((root / path).read_text().splitlines()) + 1))
  return added_lines(run('diff', '--unified=0', '--no-ext-diff', base, '--', path.as_posix(), root=root))


def function_scopes(source: str) -> list[FunctionScope]:
  tree = ast.parse(source)
  scopes: list[FunctionScope] = []

  class Visitor(ast.NodeVisitor):
    prefix: tuple[str, ...] = ()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
      self.prefix = (*self.prefix, node.name)
      self.generic_visit(node)
      self.prefix = self.prefix[:-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
      self.visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
      self.visit_function(node)

    def visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
      decorators = [decorator.lineno for decorator in node.decorator_list]
      body_start = min((child.lineno for child in node.body), default=node.end_lineno or node.lineno)
      scopes.append(FunctionScope('.'.join((*self.prefix, node.name)), min([node.lineno, *decorators]), node.end_lineno or node.lineno,
                                  body_start - 1, node))
      self.prefix = (*self.prefix, node.name)
      self.generic_visit(node)
      self.prefix = self.prefix[:-1]

  Visitor().visit(tree)
  return scopes


def changed_scopes(source: str, lines: set[int]) -> list[FunctionScope]:
  return [scope for scope in function_scopes(source) if any(scope.start <= line <= scope.end for line in lines)]


def diagnostic_fingerprint(line: str, root: Path) -> tuple[Path, str, str, str] | None:
  match = ERROR.match(line)
  if not match:
    return None
  path = Path(match['path'])
  if path.is_absolute():
    try:
      path = path.relative_to(root)
    except ValueError:
      return None
  if not (root / path).exists() and path.parts[:1] == ('openpilot',):
    path = Path(*path.parts[1:])
  source_path = root / path
  line_number = int(match['line'])
  if not source_path.exists() or line_number > len(source_path.read_text().splitlines()):
    return None
  scope = next((scope.name for scope in function_scopes(source_path.read_text()) if scope.start <= line_number <= scope.end), '<module>')
  source_line = source_path.read_text().splitlines()[line_number - 1].strip()
  return path, scope, source_line, line.split('error:', 1)[1].strip()


def baseline_fingerprints(base: str, root: Path, paths: Sequence[Path]) -> set[tuple[Path, str, str, str]]:
  with tempfile.TemporaryDirectory(prefix='bluepilot-types-') as directory:
    checkout = Path(directory) / 'base'
    subprocess.run(['git', 'worktree', 'add', '--detach', str(checkout), base], cwd=root, check=True, capture_output=True)
    try:
      shutil.copy(root / 'mypy.ini', checkout / 'mypy.ini')
      existing = [path for path in paths if (checkout / path).exists()]
      if not existing:
        return set()
      result = mypy(checkout, existing)
      return {fingerprint for line in (result.stdout + result.stderr).splitlines()
              if (fingerprint := diagnostic_fingerprint(line, checkout)) is not None}
    finally:
      subprocess.run(['git', 'worktree', 'remove', '--force', str(checkout)], cwd=root, check=True, capture_output=True)


def annotation_failures(source: str, lines: set[int]) -> list[str]:
  failures: list[str] = []
  for scope in function_scopes(source):
    if not any(scope.start <= line <= scope.end for line in lines):
      continue
    node = scope.node
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg:
      arguments.append(node.args.vararg)
    if node.args.kwarg:
      arguments.append(node.args.kwarg)
    missing = [argument.arg for argument in arguments if argument.arg not in {'self', 'cls'} and argument.annotation is None]
    if missing or node.returns is None:
      details = [*missing, *(['return'] if node.returns is None else [])]
      failures.append(f'{scope.name}: missing annotation for {", ".join(details)}')
  return failures


def relevant_errors(
  output: str, root: Path, changes: dict[Path, set[int]], inherited: set[tuple[Path, str, str, str]] | None = None,
) -> tuple[list[str], list[str]]:
  relevant: list[str] = []
  unparsed: list[str] = []
  for line in output.splitlines():
    if 'error:' not in line:
      continue
    match = ERROR.match(line)
    if not match:
      unparsed.append(line)
      continue
    path = Path(match['path'])
    if path.is_absolute():
      try:
        path = path.relative_to(root)
      except ValueError:
        unparsed.append(line)
        continue
    if not (root / path).exists() and path.parts[:1] == ('openpilot',):
      path = Path(*path.parts[1:])
    changed = changes.get(path)
    if changed is None:
      unparsed.append(line)
      continue
    line_number = int(match['line'])
    source = (root / path).read_text()
    if line_number in changed or any(scope.start <= line_number <= scope.end for scope in changed_scopes(source, changed)):
      if line_number not in changed and inherited and diagnostic_fingerprint(line, root) in inherited:
        continue
      relevant.append(line)
  return relevant, unparsed


def mypy(root: Path, paths: Sequence[Path]) -> subprocess.CompletedProcess[str]:
  command = [sys.executable, '-m', 'mypy']
  version_result = subprocess.run([*command, '--version'], cwd=root, text=True, capture_output=True)
  if version_result.returncode:
    command = ['uv', 'tool', 'run', '--from', f'mypy=={MYPY_VERSION}', 'mypy']
    version_result = subprocess.run([*command, '--version'], cwd=root, text=True, capture_output=True, check=True)
  version = version_result.stdout
  if MYPY_VERSION not in version:
    raise RuntimeError(f'Expected mypy {MYPY_VERSION}, got {version.strip()}')
  return subprocess.run([*command, '--config-file', 'mypy.ini', *map(str, paths)],
                        cwd=root, text=True, capture_output=True)


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--base', help='comparison commit; defaults to the bp-dev-models merge base')
  args = parser.parse_args(argv)
  root = Path(__file__).resolve().parents[1]
  base = args.base or base_commit(root)
  paths = changed_python_files(base, root)
  if not paths:
    print('No changed Python files.')
    return 0
  changes = {path: file_changes(base, path, root) for path in paths}
  annotations = [f'{path}: {failure}' for path in paths for failure in annotation_failures((root / path).read_text(), changes[path])]
  result = mypy(root, paths)
  inherited = baseline_fingerprints(base, root, paths)
  relevant, unparsed = relevant_errors(result.stdout + result.stderr, root, changes, inherited)
  if annotations or relevant or unparsed or (result.returncode and not result.stdout and not result.stderr):
    print('\n'.join([*annotations, *relevant, *unparsed]))
    return 1
  print(f'Branch type gate passed for {len(paths)} changed Python files against {base[:12]}.')
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
