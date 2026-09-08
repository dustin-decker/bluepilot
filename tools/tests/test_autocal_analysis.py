import numpy as np
from pathlib import Path

from openpilot.tools.autocal_analysis import COLUMNS, spectrum, windows
from openpilot.tools.autocal_validation import fit_factors, response_fit, validate


def test_windows_spectrum_and_response() -> None:
  data = np.zeros((800, len(COLUMNS)))
  data[:, 0] = np.arange(800) * 0.05
  data[:, 1] = 30
  data[:, 8] = 1
  data[200, 6] = 1
  clean = list(windows(data, seconds=5))
  assert clean and all(not np.any((w[:, 0] >= 10) & (w[:, 0] < 13)) for w in clean)
  assert len(list(windows(data, seconds=20, driver_clear=False))) == 2
  assert abs(spectrum(np.sin(2 * np.pi * data[:, 0]))[0] - 1.0) < 0.01

  rng = np.random.default_rng(42)
  synthetic = []
  for _ in range(8):
    w = np.zeros((600, len(COLUMNS)))
    u = rng.normal(0, 0.01, len(w))
    y = np.zeros(len(w))
    for i in range(3, len(w)):
      y[i] = 0.9 * y[i - 1] - 0.15 * y[i - 2] + 0.6 * u[i - 3]
    w[:, 7], w[:, 4] = u, np.rad2deg(y)
    synthetic.append(w)
  model = response_fit(synthetic[:6])
  assert model is not None
  assert model['delay_samples'] == 3
  np.testing.assert_allclose(model['coefficients'], [0.9, -0.15, 0.6], atol=0.01)
  assert validate(synthetic[6:], model)['variance_explained'] > 0.99


def test_factor_units() -> None:
  from opendbc.sunnypilot.car.ford.angle_autocal import speed_alpha

  samples = []
  for v in np.linspace(10, 30, 60):
    a = speed_alpha(v)
    gain = (1 - a) * 1.3 * 1.1 + a * 0.85 * 1.2
    samples.append(['route', v, v, 0.001, 0.001, gain, 1.0])
  np.testing.assert_allclose(fit_factors(samples, 0.85), [1.1, 1.2], atol=1e-6)


def test_native_ford_wire_decoder() -> None:
  from types import SimpleNamespace
  from openpilot.tools.ford_process_replay import angles

  data = bytearray(8)
  data[0] = 1 << 4
  data[3], data[4] = 1069 >> 6, (1069 & 63) << 2
  event = SimpleNamespace(which=lambda: 'sendcan', logMonoTime=2_000_000_000, sendcan=[SimpleNamespace(address=982, src=0, dat=data)])
  np.testing.assert_allclose(angles([event]), [[2.0, 0.0345, 1.0]])
  assert angles([]).shape == (0, 3)


def test_source_provenance_distinguishes_dirty_and_archive(tmp_path: Path) -> None:
  import subprocess
  from openpilot.tools.lib.source_provenance import source_provenance

  assert source_provenance('claimed', tmp_path)['dirty'] is None
  subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
  subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                  'commit', '-q', '--allow-empty', '-m', 'test'], check=True)
  clean = source_provenance('claimed', tmp_path)
  assert clean['commit'] != 'claimed' and not clean['dirty']
  (tmp_path / 'controller.py').write_text('changed')
  dirty = source_provenance('claimed', tmp_path)
  assert dirty['dirty']
  assert isinstance(dirty['status'], str) and 'controller.py' in dirty['status']
