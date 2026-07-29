import pytest

from openpilot.bluepilot.backend.vasm import decode_json_object, normalize_vasm_config


def test_normalize_vasm_config_accepts_bounded_polygons():
  config = normalize_vasm_config({
    "width": 1920,
    "height": 1080,
    "poly_left": [[1.2, 2.7], [100, 3], [90, 200]],
    "poly_right": [],
  })

  assert config == {
    "width": 1920,
    "height": 1080,
    "poly_left": [[1, 3], [100, 3], [90, 200]],
    "poly_right": [],
  }


@pytest.mark.parametrize("config", (
  {},
  {"width": 1920, "height": 1080, "poly_left": [], "poly_right": []},
  {"width": 1920, "height": 1080, "poly_left": [[-1, 1], [2, 2], [3, 3]], "poly_right": []},
  {"width": 1920, "height": 1080, "poly_left": [[1, 1], [2, 2]], "poly_right": []},
))
def test_normalize_vasm_config_rejects_unsafe_config(config):
  with pytest.raises(ValueError):
    normalize_vasm_config(config)


@pytest.mark.parametrize("raw", (
  '{"width":1928,"height":1208,"poly_left":[],"poly_right":[]}',
  b'{"width":1928,"height":1208,"poly_left":[],"poly_right":[]}',
))
def test_decode_vasm_config_from_params_payload(raw):
  assert decode_json_object(raw) == {
    "width": 1928,
    "height": 1208,
    "poly_left": [],
    "poly_right": [],
  }


@pytest.mark.parametrize("raw", (None, "", "invalid", [], 1))
def test_decode_vasm_config_rejects_non_objects(raw):
  assert decode_json_object(raw) == {}
