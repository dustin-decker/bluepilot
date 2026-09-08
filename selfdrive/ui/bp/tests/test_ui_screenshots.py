"""Opt-in visual regression tests; missing baselines fail, never auto-approve."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.getenv('BP_UI_SCREENSHOTS') != '1', reason='opt in with mise run ui:screenshots; requires host graphics and built fonts')
@pytest.mark.parametrize('variant,expected', [('tici', 14), ('mici', 4)])
def test_ui_screenshots(variant, expected, tmp_path):
  from PIL import Image, ImageChops
  from openpilot.common.basedir import BASEDIR

  root = Path(BASEDIR)
  baseline = Path(__file__).with_name('screenshots') / sys.platform
  output = root / '.cache/ui-screenshots' / sys.platform
  output.mkdir(parents=True, exist_ok=True)
  subprocess.run([sys.executable, '-m', 'openpilot.tools.ui_screenshots', '--variant', variant, '--output', str(tmp_path)],
                 check=True, timeout=90, cwd=root)
  images = sorted(tmp_path.glob(f'{variant}-*.png'))
  assert len(images) == expected
  failures = []
  for actual_path in images:
    actual = Image.open(actual_path).convert('RGB')
    actual.save(output / actual_path.name)
    reference_path = baseline / actual_path.name
    if os.getenv('BP_UI_UPDATE') == '1':
      baseline.mkdir(parents=True, exist_ok=True)
      actual.save(reference_path)
      continue
    if not reference_path.exists():
      failures.append(f'{actual_path.name}: missing baseline (review output, then ui:screenshots:update)')
      continue
    reference = Image.open(reference_path).convert('RGB')
    if actual.size != reference.size:
      failures.append(f'{actual_path.name}: dimensions differ')
    else:
      diff = ImageChops.difference(actual, reference)
      if diff.getbbox():
        diff.save(output / f'{actual_path.stem}-diff.png')
        failures.append(f'{actual_path.name}: pixels differ')
  assert not failures, '\n'.join(failures) + f'\nActual/diff images: {output}'
