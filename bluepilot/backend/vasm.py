"""BluePilot Portal helpers for Vision Adjacent Spot Monitoring (V-ASM)."""

from __future__ import annotations

import json
import math
import subprocess
import time
from typing import Any


MAX_POLYGON_POINTS = 64


def decode_json_object(value: Any) -> dict[str, Any]:
  if isinstance(value, bytes):
    value = value.decode("utf-8", errors="replace")
  if isinstance(value, str):
    try:
      value = json.loads(value)
    except json.JSONDecodeError:
      return {}
  return value if isinstance(value, dict) else {}


def normalize_vasm_config(data: Any) -> dict[str, Any]:
  if not isinstance(data, dict):
    raise ValueError("Configuration must be a JSON object.")

  try:
    width = int(data.get("width", 0))
    height = int(data.get("height", 0))
  except (TypeError, ValueError) as exc:
    raise ValueError("Camera dimensions must be integers.") from exc
  if width <= 0 or height <= 0 or width > 8192 or height > 8192:
    raise ValueError("Camera dimensions are invalid.")

  def normalize_polygon(key: str) -> list[list[int]]:
    polygon = data.get(key, [])
    if not isinstance(polygon, list) or len(polygon) > MAX_POLYGON_POINTS:
      raise ValueError(f"{key} must contain at most {MAX_POLYGON_POINTS} points.")
    if polygon and len(polygon) < 3:
      raise ValueError(f"{key} requires at least 3 points.")

    normalized = []
    for point in polygon:
      if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError(f"{key} contains an invalid point.")
      try:
        x, y = float(point[0]), float(point[1])
      except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} contains a non-numeric point.") from exc
      if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= width and 0 <= y <= height):
        raise ValueError(f"{key} contains a point outside the camera frame.")
      normalized.append([round(x), round(y)])
    return normalized

  config = {
    "width": width,
    "height": height,
    "poly_left": normalize_polygon("poly_left"),
    "poly_right": normalize_polygon("poly_right"),
  }
  if not config["poly_left"] and not config["poly_right"]:
    raise ValueError("At least one window polygon is required.")
  return config


def capture_live_driver_jpeg() -> bytes | None:
  """Capture one live driver-camera frame, starting camerad temporarily if needed."""
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  from openpilot.system.manager.process_config import managed_processes

  started_camerad = False
  try:
    try:
      subprocess.check_call(["pgrep", "camerad"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
      managed_processes["camerad"].start()
      started_camerad = True

    client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_DRIVER, True)
    if not client.connect(True):
      return None

    if started_camerad:
      settle_deadline = time.monotonic() + 4.0
      while time.monotonic() < settle_deadline:
        client.recv(timeout_ms=100)

    buf = client.recv(timeout_ms=5000)
    if buf is None:
      return None

    import cv2
    import numpy as np

    raw = np.frombuffer(buf.data, dtype=np.uint8).reshape((len(buf.data) // buf.stride, buf.stride))
    raw = raw[:, :buf.width]
    bgr = cv2.cvtColor(raw, cv2.COLOR_YUV2BGR_NV12)
    encoded, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return jpeg.tobytes() if encoded else None
  except Exception:
    return None
  finally:
    if started_camerad:
      managed_processes["camerad"].stop()
