"""Conservative OSM corroboration; a map stop node is not a surveyed stop line."""
from __future__ import annotations

from typing import Any
from collections.abc import Sequence
from pathlib import Path
import json
import math
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import angle_difference, xy


def fetch(bounds: Sequence[float], destination: Path) -> dict[str, Any]:
  south, west, north, east = map(float, bounds)
  if not (-90 <= south < north <= 90 and -180 <= west < east <= 180 and north - south <= 0.1 and east - west <= 0.1):
    raise ValueError("Use a bounded area no larger than 0.1 degrees per side")
  query = f'[out:json][timeout:25];node[highway=stop]({south},{west},{north},{east});(._;way(bn););out body;>;out skel qt;'
  request = Request("https://overpass-api.de/api/interpreter", data=urlencode({"data": query}).encode(),
                    headers={"User-Agent": "BluePilot-personal-stop-review/1.0"})
  with urlopen(request, timeout=35) as response:
    raw = response.read(8 * 1024 * 1024 + 1)
  if len(raw) > 8 * 1024 * 1024:
    raise ValueError("Map response too large")
  data = json.loads(raw)
  if "remark" in data:
    raise ValueError("Map query incomplete: " + data["remark"])
  data['retrieved_utc'] = datetime.now(UTC).isoformat()
  destination.parent.mkdir(parents=True, exist_ok=True)
  destination.write_text(json.dumps(data))
  return data


def corroborate(observation: dict[str, Any], data: dict[str, Any]) -> dict[str, Any] | None:
  elements = data.get("elements", [])
  nodes: dict[int, dict[str, Any]] = {}
  for element in elements:
    if element.get('type') == 'node':
      # Overpass's recursive skeleton output can repeat an already tagged node.
      nodes[element['id']] = {**nodes.get(element['id'], {}), **element}
  confirmed = []
  for node in nodes.values():
    tags = node.get("tags", {})
    if tags.get("highway") != "stop" or any("conditional" in k for k in tags):
      continue
    if math.hypot(*xy(node["lat"], node["lon"], (observation["lat"], observation["lon"]))) > 20:
      continue
    # Intersection-centre/all-way tagging says a stop exists, not which learned
    # road is this approach. Leave ambiguous tags for explicit human review.
    direction = tags.get("direction", tags.get("stop:direction"))
    ways = [e for e in elements if e.get("type") == "way" and node["id"] in e.get("nodes", [])]
    if len(ways) != 1 or direction not in ("forward", "backward"):
      continue
    way = ways[0]
    index = way["nodes"].index(node["id"])
    previous = index - 1 if direction == "forward" else index + 1
    if not 0 <= previous < len(way["nodes"]) or way["nodes"][previous] not in nodes:
      continue
    other = nodes[way["nodes"][previous]]
    dx, dy = xy(node["lat"], node["lon"], (other["lat"], other["lon"]))
    if observation["bearing"] is not None and angle_difference(observation["bearing"], math.degrees(math.atan2(dx, dy))) < 20:
      confirmed.append({"node": node["id"], "way": way["id"], "direction": direction,
                        "retrieved_utc": data.get('retrieved_utc'),
                        "attribution": "© OpenStreetMap contributors", "source": "https://www.openstreetmap.org/node/" + str(node["id"])})
  return confirmed[0] if len(confirmed) == 1 else None
