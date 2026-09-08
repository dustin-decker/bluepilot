import io
import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from .api import handle, import_observations
from .core import MAX_SPEED, Observer, Sample, StopController, match_path
from .daemon import find_target
from .store import Store
from .osm import corroborate
from .types import Target


def observation(drive: str = 'drive-1', lead: float | None = None, gear: str = 'drive', automatic: bool = False) -> dict[str, Any]:
  observer = Observer(drive)
  result = None
  for i in range(321):
    t = i / 10
    sample = Sample(t=t, utc=1800000000 + t, speed=5 if t < 30 else 0,
                    lat=37 + min(t, 30) * 5 / 111320, lon=-120, accuracy=0.3,
                    brake=t >= 28, gear=gear, lead=lead, long_active=automatic)
    result = observer.update(sample) or result
  assert result is not None
  return result


def target(distance: float = 150, ready: bool = True) -> Target:
  return {"id": "stop-1", "distance": distance, "accuracy": 0.3, "ready": ready}


def test_manual_and_queue_are_different_evidence() -> None:
  assert observation()["reasons"] == []
  assert "queue" in observation(lead=6.8)["reasons"]
  assert "maneuver" in observation(gear="reverse")["reasons"]
  assert "not_manual" in observation(automatic=True)["reasons"]


def test_review_queue_keeps_pending_observations_not_completed_exclusions(tmp_path: Path) -> None:
  store = Store(tmp_path)
  first = store.add('car', observation(lead=6.8))
  assert store.list()[0]['status'] == 'excluded'  # Radar-flagged queues need no manual dismissal.
  second = store.add('car', observation(drive='drive-2'))
  assert store.list()[0]['status'] == 'review'
  store.review(first['stop'], 'exclude', first['id'], 'maneuver')
  stop = store.list()[0]
  assert stop['status'] == 'review' and stop['position']['id'] == second['id']
  store.review(second['stop'], 'exclude', second['id'], 'wrong_approach')
  stop = Store(tmp_path).list()[0]
  assert stop['status'] == 'excluded' and len(stop['observations']) == 2
  assert all(o['excluded'] for o in stop['observations'])
  # Honor exclusions already saved by the original release, without losing history.
  with sqlite3.connect(store.path) as db:
    for row in db.execute('SELECT id,data FROM observations').fetchall():
      data = json.loads(row[1])
      data.pop('excluded')
      db.execute('UPDATE observations SET data=? WHERE id=?', (json.dumps(data), row[0]))
  assert Store(tmp_path).list()[0]['status'] == 'excluded'
  store.add('car', observation(drive='drive-3'))
  assert store.list()[0]['status'] == 'review'


def test_observe_keeps_preview_during_manual_driving() -> None:
  controller = StopController()
  for pedal in ('gas', 'brake'):
    sample = Sample(t=1, utc=1, speed=10, gas=pedal == 'gas', brake=pedal == 'brake')
    state = controller.update(sample, 'observe', target())
    assert state['state'] == 'approach' and not state['apply']
  assert not controller.suppressed


def test_osm_direction_and_repeated_skeleton_nodes() -> None:
  obs = observation()
  node = {'type': 'node', 'id': 1, 'lat': obs['lat'], 'lon': obs['lon'], 'tags': {'highway': 'stop', 'direction': 'forward'}}
  other = {'type': 'node', 'id': 2, 'lat': obs['lat'] - .001, 'lon': obs['lon']}
  way = {'type': 'way', 'id': 3, 'nodes': [2, 1]}
  data = {'elements': [node, other, way, {k: v for k, v in node.items() if k != 'tags'}]}
  matched = corroborate(obs, data)
  assert matched is not None and matched['node'] == 1
  assert corroborate(dict(obs, bearing=180), data) is None
  data['elements'].append({'type': 'way', 'id': 4, 'nodes': [1, 2]})
  assert corroborate(obs, data) is None


def test_portal_driving_key_and_unreadable_state_block_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  from bluepilot.backend.network import utils
  store = Store(tmp_path)
  def driving(key: str) -> bool:
    assert key == 'IsOnroad'
    return True
  monkeypatch.setattr(utils, 'params', SimpleNamespace(get=driving))
  handler = Handler({'mode': 'observe'})
  handle(handler, '/api/learned-stops/mode', 'POST', utils.is_onroad, store)
  assert handler.status == 403 and store.mode() == 'off'
  def unreadable(key: str) -> None:
    raise OSError('unavailable')
  monkeypatch.setattr(utils, 'params', SimpleNamespace(get=unreadable))
  assert utils.is_onroad()
  for value in (None, b'', '', True, b'1'):
    monkeypatch.setattr(utils, 'params', SimpleNamespace(get=lambda key, value=value: value))
    assert utils.is_onroad()
  for value in (False, b'0', '0', 'False'):
    monkeypatch.setattr(utils, 'params', SimpleNamespace(get=lambda key, value=value: value))
    assert not utils.is_onroad()


def test_corrupt_video_does_not_abandon_later_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  from . import evidence
  from openpilot.tools.lib.vidindex import VideoFileInvalid
  store = Store(tmp_path)
  ids = []
  for i, route in enumerate(('bad', 'good')):
    obs = dict(observation(str(i)), utc=1800000000 + i * 86400,
               frames=[{'route': route, 'camera': 'front', 'offset': -8}])
    ids.append(store.add('car', obs)['id'])
  filename = 'a' * 64 + '.jpg'
  def capture(frame: dict[str, Any], logs: Any, output: Path, available_bytes: int) -> dict[str, Any]:
    if frame['route'] == 'bad':
      raise VideoFileInvalid('truncated')
    output.mkdir(exist_ok=True)
    (output / filename).write_bytes(b'image')
    return dict(frame, file=filename)
  monkeypatch.setattr(evidence, 'capture', capture)
  errors = evidence.collect(store, tmp_path)
  assert len(errors) == 1 and errors[0]['observation'] == ids[0]
  assert store.list()[0]['observations'][1]['evidence'][0]['file'] == filename
  with pytest.raises(ValueError, match='driving'):
    evidence.remove_evidence(store, ids[1], lambda: True)
  assert (tmp_path / 'evidence' / filename).exists()
  evidence.remove_evidence(store, ids[1])
  assert not (tmp_path / 'evidence' / filename).exists()
  assert store.list()[0]['observations'][1]['evidence_removed']


def test_backup_contains_consistent_database_and_images(tmp_path: Path) -> None:
  store = Store(tmp_path)
  store.add('car', observation())
  (tmp_path / 'evidence').mkdir()
  name = 'b' * 64 + '.jpg'
  (tmp_path / 'evidence' / name).write_bytes(b'image')
  class BackupHandler(Handler):
    def send_file_response(self, path: Path, mime: str, download: bool) -> None:
      with zipfile.ZipFile(path) as archive:
        assert archive.read('evidence/' + name) == b'image'
        snapshot = tmp_path / 'snapshot.sqlite3'
        snapshot.write_bytes(archive.read('stops.sqlite3'))
        with sqlite3.connect(snapshot) as db:
          assert db.execute('SELECT count(*) FROM observations').fetchone()[0] == 1
  assert handle(BackupHandler(), '/api/learned-stops/backup', 'GET', lambda: False, store)


def test_import_discards_unknown_fields_and_rejects_large_flag_objects(tmp_path: Path) -> None:
  store = Store(tmp_path)
  obs = dict(observation(), junk='x' * 100000)
  body = {'version': 1, 'stops': [{'vehicle': 'car', 'observations': [obs]}]}
  import_observations(store, body)
  assert 'junk' not in store.list()[0]['observations'][0]
  obs['manual'] = {'junk': 'x' * 100000}
  with pytest.raises(ValueError, match='flags'):
    import_observations(store, body)


def test_missing_accuracy_is_not_perfect(tmp_path: Path) -> None:
  store = Store(tmp_path)
  o = observation()
  o["accuracy"] = 0
  store.add("car", o)
  saved = store.list()[0]
  assert saved["position"]["accuracy"] is None
  assert not saved["ready"]


def test_three_independent_drives_and_confirmation(tmp_path: Path) -> None:
  store = Store(tmp_path)
  for i in range(3):
    o = observation(f"drive-{i}")
    o["utc"] += i * 86400
    store.add("car", o)
    store.add("car", o)
  stop = store.list()[0]
  assert len(stop["observations"]) == 3 and stop["visits"] == 3
  assert not stop["ready"]
  store.review(stop["id"], "confirm")
  assert store.list()[0]['qualified'] and not store.list()[0]['ready']
  store.review(stop['id'], 'approve')
  assert store.list()[0]["ready"]
  store.review(stop["id"], "disable")
  assert not store.list()[0]["ready"]
  store.add("car", dict(observation("new-drive"), utc=1800500000))
  assert store.list()[0]["disabled"]


def test_segments_and_repeat_stops_do_not_make_independent_visits(tmp_path: Path) -> None:
  store = Store(tmp_path)
  for i in range(3):
    o = observation()
    o["utc"] += i * 60
    store.add("car", o)
  assert store.list()[0]["visits"] == 1


def test_cross_road_opposite_direction_and_vehicle_are_separate(tmp_path: Path) -> None:
  store = Store(tmp_path)
  o = observation()
  store.add("car", o)
  store.add("other-car", o)
  store.add("car", dict(o, utc=o["utc"] + 30, bearing=180))
  store.add("car", dict(o, utc=o["utc"] + 60, lat=o["lat"] - 41 / 111320))
  assert len(store.list()) == 4
  sample = Sample(t=0, utc=0, speed=5, lat=o["lat"] - 30 / 111320, lon=-120)
  assert match_path(sample, o["path"], 0) == pytest.approx(30, abs=.1)
  assert match_path(sample, o["path"], 180) is None
  assert match_path(replace(sample, lon=-119.99), o["path"], 0) is None


@pytest.mark.parametrize("mode", ["off", "observe"])
def test_noncontrol_never_applies_even_with_old_latch(mode: str) -> None:
  controller = StopController(validated=True, latch={"id": "stop-1", "distance": 0, "hold": True})
  result = controller.update(Sample(t=1, utc=0, speed=0, accuracy=.3), mode, target(), engaged=True)
  assert not result["apply"] and not result["hold"]


def test_field_gate_cannot_be_bypassed_by_persisted_latch() -> None:
  controller = StopController(latch={"id": "stop-1", "distance": 0, "hold": True})
  result = controller.update(Sample(t=1, utc=0, speed=0, accuracy=.3), "control", target(), engaged=True)
  assert not result["apply"] and result["reason"] == "field_validation_required"


@pytest.mark.parametrize("speed", [0, 10, 20, MAX_SPEED])
def test_start_constraint_within_speed_envelope(speed: float) -> None:
  controller = StopController(validated=True)
  distance = speed**2 / 2.4 + speed + 30
  result = controller.update(Sample(t=1, utc=0, speed=speed, accuracy=.3), "control", target(distance), engaged=True)
  assert result["apply"]


def test_above_speed_late_stop_and_uncertain_position_are_rejected() -> None:
  s = Sample(t=1, utc=0, speed=MAX_SPEED + .1, accuracy=.3)
  assert StopController(True).update(s, "control", target(500), True)["reason"] == "above_70_mph"
  assert StopController(True).update(replace(s, speed=20), "control", target(5), True)["reason"] == "insufficient_distance"
  assert StopController(True).update(replace(s, speed=10, accuracy=4), "control", target(), True)["reason"] == "position_uncertain"


def test_hold_survives_missing_target_and_restart_until_accelerator() -> None:
  controller = StopController(True, {"id": "stop-1", "distance": 0, "hold": True})
  s = Sample(t=1, utc=0, speed=0, accuracy=None)
  status = controller.update(s, "control", engaged=True)
  assert status["hold"] and status["apply"] and status["takeover"]
  restarted = StopController(True, json.loads(json.dumps(controller.latch)))
  assert restarted.update(replace(s, t=2), "control", engaged=True)["hold"]
  assert not restarted.update(replace(s, t=3, gas=True), "control", engaged=True)["apply"]
  assert not restarted.update(replace(s, t=4), "control", target(20), True)["apply"]


def test_queue_stop_does_not_finish_target_and_gps_cannot_move_it_away() -> None:
  controller = StopController(True, {"id": "stop-1", "distance": 41, "hold": False})
  s = Sample(t=1, utc=0, speed=0, accuracy=.3)
  state = controller.update(s, "control", target(100), True)
  assert state["hold"] and not state["at_reference"]
  assert state["reason"] == "stopped_before_reference"
  assert controller.latch is not None and controller.latch["distance"] == 41


def test_gps_jump_closer_does_not_move_latched_reference() -> None:
  controller = StopController(True, {'id': 'stop-1', 'distance': 100, 'hold': False})
  state = controller.update(Sample(t=1, utc=1, speed=20, accuracy=.3), 'control', target(5), True)
  assert state['takeover'] and state['distance'] == 100


class Handler:
  def __init__(self, body: dict[str, Any] | None = None) -> None:
    payload = json.dumps(body or {}).encode()
    self.headers = {"Content-Length": str(len(payload))}
    self.rfile = io.BytesIO(payload)

  def send_json_response(self, data: Any, status: int = 200) -> None:
    self.response, self.status = data, status


def test_offroad_guard_rechecks_after_body(tmp_path: Path) -> None:
  handler = Handler({"mode": "observe"})
  states = iter([False, True])
  store = Store(tmp_path)
  handle(handler, "/api/learned-stops/mode", "POST", lambda: next(states), store)
  assert handler.status == 403 and store.mode() == "off"


def test_import_retains_confirmation_but_not_derived_readiness_or_file_paths(tmp_path: Path) -> None:
  store = Store(tmp_path)
  o = observation()
  o["evidence"] = [{"file": "../../secret"}]
  import_observations(store, {"version": 1, "stops": [{"vehicle": "car", "confirmed": "user", "ready": True, "observations": [o]}]})
  saved = store.list()[0]
  assert not saved["ready"] and saved["confirmed"] == "user"
  assert saved["position"]["evidence"] == []
  assert "imported_unverified" not in saved["position"]["reasons"]


def test_export_import_restores_approval_reference_exclusions_and_disabled_state(tmp_path: Path) -> None:
  source, destination = Store(tmp_path / 'source'), Store(tmp_path / 'destination')
  for drive in ('first', 'second', 'third'):
    source.add('car', observation(drive))
  stop = source.list()[0]
  source.review(stop['id'], 'confirm')
  reference = stop['observations'][1]['id']
  source.review(stop['id'], 'reference', reference)
  source.review(stop['id'], 'approve')
  excluded = dict(observation('excluded'), lat=38)
  entry = source.add('car', excluded)
  source.review(entry['stop'], 'exclude', entry['id'], 'maneuver')
  source.review(entry['stop'], 'disable')
  body: dict[str, Any] = {'version': 1, 'mode': 'control', 'stops': source.list()}
  destination.set_mode('observe')
  import_observations(destination, body)
  import_observations(destination, body)  # Retry remaps IDs without duplicate visits.
  restored, dismissed = destination.list()
  assert restored['ready'] and restored['approved'] and restored['confirmed'] == 'user'
  assert restored['position']['drive'] == 'second' and restored['reference'] == restored['position']['id']
  assert restored['visits'] == 3 and len(restored['observations']) == 3
  assert dismissed['disabled'] and dismissed['observations'][0]['excluded']
  assert destination.mode() == 'observe'
  # A false derived readiness flag cannot bypass the actual positioning gate.
  body['stops'][0]['observations'][0]['accuracy'] = 5
  import_observations(destination, body)
  assert destination.list()[0]['approved'] and not destination.list()[0]['ready']


def test_import_rolls_back_on_driving_and_conflicting_reference(tmp_path: Path) -> None:
  source, destination = Store(tmp_path / 'source'), Store(tmp_path / 'destination')
  source.add('car', observation())
  source.add('car', observation('second'))
  body: dict[str, Any] = {'version': 1, 'stops': source.list()}
  states = iter([False, True])
  with pytest.raises(ValueError, match='cancelled'):
    import_observations(destination, body, lambda: next(states))
  assert destination.list() == []
  body['stops'][0].update(confirmed='user', approved=1, reference='missing')
  with pytest.raises(ValueError, match='reference'):
    import_observations(destination, body)
  assert destination.list() == []


def test_ambiguous_matching_never_chooses_a_nearby_parallel_target(tmp_path: Path) -> None:
  store = Store(tmp_path)
  o = observation()
  store.add("car", o)
  stops = store.list()
  sample = Sample(t=0, utc=0, speed=5, lat=o["lat"] - 30 / 111320, lon=-120)
  assert find_target(sample, 0, stops)
  assert find_target(sample, 0, stops + [dict(stops[0], id="competing")]) is None
