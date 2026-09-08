"""Local stop memory. Only reviewed semantics can promote observed locations."""
from __future__ import annotations

from typing import Any, cast
import builtins
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import uuid

from .core import CONTROL_VALIDATED, angle_difference, xy


def data_root() -> Path:
  return Path(os.environ.get("BP_LEARNED_STOPS_ROOT", "/data/bluepilot/learned_stops" if Path("/data/params").exists()
                             else str(Path.home() / ".local/share/bluepilot/learned_stops")))


class Store:
  def __init__(self, root: str | Path | None = None) -> None:
    self.root = Path(root) if root is not None else data_root()
    self.root.mkdir(parents=True, exist_ok=True)
    self.path = self.root / "stops.sqlite3"
    with closing(self.connect()) as db, db:
      if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
        raise ValueError('Unsupported stop database version; use a compatible BluePilot build')
      db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO settings VALUES ('mode', 'off');
        CREATE TABLE IF NOT EXISTS stops (
          id TEXT PRIMARY KEY, vehicle TEXT NOT NULL, confirmed TEXT NOT NULL DEFAULT '',
          disabled INTEGER NOT NULL DEFAULT 0, reference TEXT, approved INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS observations (
          id TEXT PRIMARY KEY, stop TEXT NOT NULL REFERENCES stops(id),
          vehicle TEXT NOT NULL, drive TEXT NOT NULL, utc REAL NOT NULL, data TEXT NOT NULL,
          UNIQUE(vehicle, drive, utc));
        PRAGMA user_version=1;
      """)
      if 'approved' not in {r[1] for r in db.execute('PRAGMA table_info(stops)')}:
        db.execute('ALTER TABLE stops ADD COLUMN approved INTEGER NOT NULL DEFAULT 0')

  def connect(self) -> sqlite3.Connection:
    db = sqlite3.connect(self.path, timeout=.2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db

  def mode(self) -> str:
    with closing(self.connect()) as db:
      return db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0]

  def set_mode(self, mode: str | None) -> None:
    if mode not in ("off", "observe", "control"):
      raise ValueError("Unknown mode")
    if mode == "control" and not CONTROL_VALIDATED:
      raise ValueError("Control requires physical stopping validation; use Observe")
    with closing(self.connect()) as db, db:
      db.execute("UPDATE settings SET value=? WHERE key='mode'", (mode,))

  def list(self, vehicle: str | None = None) -> list[dict[str, Any]]:
    with closing(self.connect()) as db:
      rows = db.execute("SELECT * FROM stops" + (" WHERE vehicle=?" if vehicle else ""), (vehicle,) if vehicle else ()).fetchall()
      result = []
      for row in rows:
        stop = dict(row)
        corroboration = db.execute("SELECT value FROM settings WHERE key=?", ('osm:' + stop['id'],)).fetchone()
        stop['map_evidence'] = json.loads(corroboration[0]) if corroboration else None
        observations = []
        for obs in db.execute("SELECT id,data FROM observations WHERE stop=? ORDER BY utc", (stop["id"],)):
          data = json.loads(obs["data"])
          # Earlier manual exclusions persisted the observation ID inside data;
          # automatic exclusions never did. Keep those completed reviews honored.
          excluded = 'queue' in data['reasons'] or data.get('excluded', bool(data.get('id') == obs['id'] and data['reasons']))
          observations.append(dict(data, id=obs["id"], excluded=excluded))
        pending = [o for o in observations if not o['excluded']]
        good = [o for o in observations if not o["reasons"] and not o['excluded']]
        visits = len({o["drive"] for o in good})
        reference = next((o for o in good if o["id"] == stop["reference"]), None)
        if reference is None and good:
          reference = min(good, key=lambda o: o["accuracy"])
        position = reference or (pending or observations)[-1]
        spread = max((math.hypot(*xy(o["lat"], o["lon"], (position["lat"], position["lon"]))) for o in good), default=0)
        accurate = bool(good and all(o["accuracy"] <= 2 for o in good) and spread <= 2)
        qualified = bool(visits >= 3 and accurate and stop["confirmed"] and not stop["disabled"])
        ready = qualified and bool(stop['approved']) and stop['reference'] == position['id']
        stop.update(observations=observations, visits=visits, ready=ready, qualified=qualified, position=position,
                    spread=spread, accuracy_ok=accurate, status="disabled" if stop["disabled"] else
                    "excluded" if not pending else "approved" if ready else "review" if not stop["confirmed"] or qualified else "learning")
        result.append(stop)
      return result

  @staticmethod
  def validate_observation(obs: dict[str, Any]) -> None:
    for key, lower, upper in (("lat", -90, 90), ("lon", -180, 180), ("utc", 1, 1e11)):
      value = obs.get(key)
      if type(value) not in (int, float) or not math.isfinite(cast(float, value)) or not lower <= cast(float, value) <= upper:
        raise ValueError(f"Invalid {key}")
    if not isinstance(obs.get("drive"), str) or not 1 <= len(obs["drive"]) <= 256:
      raise ValueError("Invalid drive identifier")
    bearing = obs.get("bearing")
    if bearing is not None and (not isinstance(bearing, (int, float)) or not math.isfinite(bearing) or not 0 <= bearing < 360):
      raise ValueError("Invalid approach bearing")
    if not isinstance(obs.get("reasons"), list) or len(obs["reasons"]) > 32 or any(not isinstance(s, str) or len(s) > 100 for s in obs["reasons"]):
      raise ValueError("Invalid observation exclusions")
    accuracy = obs.get("accuracy")
    if accuracy is None or not isinstance(accuracy, (int, float)) or not math.isfinite(accuracy) or accuracy <= 0:
      if "accuracy_unavailable" not in obs["reasons"]:
        obs["reasons"].append("accuracy_unavailable")
      obs["accuracy"] = None
    path = obs.setdefault("path", [])
    if not isinstance(path, list) or len(path) > 3000:
      raise ValueError("Invalid approach path")
    for point in path:
      if (not isinstance(point, list) or len(point) != 2 or
          any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in point) or
          not -90 <= point[0] <= 90 or not -180 <= point[1] <= 180):
        raise ValueError("Invalid approach coordinate")
    if bearing is None and "direction_unknown" not in obs["reasons"]:
      obs["reasons"].append("direction_unknown")
    obs.setdefault("bearing", None)
    if len(path) < 2 and "path_unavailable" not in obs["reasons"]:
      obs["reasons"].append("path_unavailable")
    if not isinstance(obs.get("source", ""), str) or len(obs.get("source", "")) > 256:
      raise ValueError("Invalid source")
    obs.setdefault("source", "")
    obs.setdefault("evidence", [])
    obs.setdefault("manual", False)
    obs.setdefault('model_stop', False)
    if type(obs['manual']) is not bool or type(obs['model_stop']) is not bool:
      raise ValueError('Invalid observation flags')
    if 't' in obs and (type(obs['t']) not in (int, float) or not math.isfinite(obs['t']) or not 0 <= obs['t'] <= 1e12):
      raise ValueError('Invalid monotonic timestamp')

  def add(self, vehicle: str, observation: dict[str, Any], candidates_cache: builtins.list[dict[str, Any]] | None = None) -> dict[str, str]:
    obs = dict(observation, reasons=list(observation["reasons"]))
    self.validate_observation(obs)
    if not isinstance(vehicle, str) or not 1 <= len(vehicle) <= 256:
      raise ValueError("Invalid vehicle")
    # ponytail: linear scan is sufficient for a personal stop library; spatial
    # indexing is appropriate if the library grows beyond a few thousand stops.
    candidates = []
    existing_stops = self.list(vehicle) if candidates_cache is None else candidates_cache
    for stop in existing_stops:
      p = stop["position"]
      if (obs["bearing"] is not None and p["bearing"] is not None and
          angle_difference(obs["bearing"], p["bearing"]) < 20 and
          math.hypot(*xy(obs["lat"], obs["lon"], (p["lat"], p["lon"]))) < 12):
        candidates.append(stop["id"])
    stop_id = candidates[0] if len(candidates) == 1 else str(uuid.uuid4())
    with closing(self.connect()) as db, db:
      existing = db.execute("SELECT id,stop FROM observations WHERE vehicle=? AND drive=? AND utc=?",
                            (vehicle, obs["drive"], obs["utc"])).fetchone()
      if existing:
        return dict(existing)
      db.execute("INSERT OR IGNORE INTO stops(id,vehicle) VALUES (?,?)", (stop_id, vehicle))
      obs_id = str(uuid.uuid4())
      db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?)",
                 (obs_id, stop_id, vehicle, obs["drive"], obs["utc"], json.dumps(obs, allow_nan=False)))
      if candidates_cache is not None and len(candidates) != 1:
        candidates_cache.append({'id': stop_id, 'position': obs})
      return {"id": obs_id, "stop": stop_id}

  def review(self, stop_id: str | None, action: str | None, observation: str | None = None, reason: str | None = None) -> None:
    stop = next((s for s in self.list() if s["id"] == stop_id), None)
    if stop is None:
      raise ValueError("Unknown stop")
    with closing(self.connect()) as db, db:
      if action in ("disable", "enable"):
        db.execute("UPDATE stops SET disabled=?, approved=0 WHERE id=?", (int(action == "disable"), stop_id))
      elif action == "confirm":
        db.execute("UPDATE stops SET confirmed='user' WHERE id=?", (stop_id,))
      elif action == 'approve':
        if not stop['qualified']:
          raise ValueError('Confirm the sign, collect three eligible independent drives, and meet position limits first')
        db.execute('UPDATE stops SET approved=1, reference=? WHERE id=?', (stop['position']['id'], stop_id))
      elif action == "reference":
        if not any(o["id"] == observation and not o["reasons"] for o in stop["observations"]):
          raise ValueError("Choose an eligible stopping observation")
        db.execute("UPDATE stops SET reference=?, approved=0 WHERE id=?", (observation, stop_id))
      elif action == "exclude":
        if reason not in ("traffic_light", "queue", "maneuver", "wrong_approach", "unclear"):
          raise ValueError("Choose an exclusion reason")
        obs = next((o for o in stop["observations"] if o["id"] == observation), None)
        if obs is None:
          raise ValueError("Unknown observation")
        obs["reasons"] = sorted(set(obs["reasons"] + [reason]))
        obs['excluded'] = True
        db.execute("UPDATE observations SET data=? WHERE id=?", (json.dumps(obs, allow_nan=False), observation))
        db.execute('UPDATE stops SET approved=0 WHERE id=?', (stop_id,))
      else:
        raise ValueError("Unknown review action")
