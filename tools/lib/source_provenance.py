"""Record the checkout actually supplying offline analysis code, including local edits."""
import subprocess
from pathlib import Path


def source_provenance(label: str | None = None, root: str | Path | None = None) -> dict[str, str | bool | None]:
  root = Path(root or Path(__file__).resolve().parents[2]).resolve()

  def git(*args: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()

  try:
    if Path(git('rev-parse', '--show-toplevel')).resolve() != root:
      raise ValueError('Archive inside another checkout')
    commit = git('rev-parse', 'HEAD')
    status = git('status', '--porcelain')
    return {'label': label, 'commit': commit, 'dirty': bool(status), 'status': status, 'diff_stat': git('diff', 'HEAD', '--stat')}
  except (subprocess.CalledProcessError, ValueError):
    return {'label': label, 'commit': None, 'dirty': None, 'note': 'Archive: supplied revision is a claim, not verified by Git.'}
