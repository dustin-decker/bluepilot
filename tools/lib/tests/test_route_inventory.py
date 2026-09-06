from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from openpilot.tools.route_inventory import summarize, segment_logs


def event(name, seconds, valid=True, **fields):
  return SimpleNamespace(which=lambda: name, logMonoTime=int(seconds * 1e9), valid=valid,
                         **{name: SimpleNamespace(**fields)})


def test_recorded_clock_not_utc_date_selects_thursday():
  wall = int(datetime(2026, 9, 4, 4, tzinfo=UTC).timestamp() * 1e9)
  events = [event('clocks', 10, valid=False, wallTimeNanos=1), event('carState', 10, vEgo=20),
            event('clocks', 11, wallTimeNanos=wall), event('carState', 12, vEgo=31)]
  row = summarize(events, ZoneInfo('America/Los_Angeles'), date(2026, 9, 3))
  assert row['local_start'] == '2026-09-03T20:59:59-07:00'
  assert row['duration_s'] == 2
  assert row['speed_20to30'] == row['speed_ge30'] == 1
  assert summarize(events, ZoneInfo('UTC'), date(2026, 9, 3)) is None


def test_missing_clock_not_invented_and_invalid_speed_excluded():
  events = [event('carState', 1, vEgo=float('nan')), event('carState', 2, valid=False, vEgo=50)]
  assert summarize(events, ZoneInfo('UTC'), date(2026, 9, 3)) is None
  row = summarize(events, ZoneInfo('UTC'))
  assert row['local_start'] is row['max_speed_mps'] is None
  assert row['invalid_speed'] == row['invalid_carState'] == 1


def test_repeated_route_start_metadata_does_not_inflate_segment_duration():
  wall = int(datetime(2026, 9, 4, 4, tzinfo=UTC).timestamp() * 1e9)
  events = [event('initData', 0), event('carParams', 0), event('carState', 1200, vEgo=20),
            event('clocks', 1201, wallTimeNanos=wall), event('carState', 1260, vEgo=20)]
  row = summarize(events, ZoneInfo('America/Los_Angeles'))
  assert row['duration_s'] == 60
  assert row['local_start'] == '2026-09-03T20:59:59-07:00'


def test_qlog_preferred_and_rlog_fallback(tmp_path):
  folder = tmp_path / 'route--0'
  folder.mkdir()
  (folder / 'rlog.zst').touch()
  assert list(segment_logs(tmp_path)) == [folder / 'rlog.zst']
  (folder / 'qlog.bz2').touch()
  assert list(segment_logs(tmp_path)) == [folder / 'qlog.bz2']


def test_ford_candidates_require_fresh_state_and_grip_cooldown():
  events = []
  for i in range(80):
    t = 1 + i / 10
    v = 20 if i < 40 else 32
    events += [event('carState', t, vEgo=v, steeringPressed=False, steeringTorque=1.0 if i == 20 else 0.0),
               event('carControl', t, latActive=True, actuators=SimpleNamespace(curvature=0.0015))]
  row = summarize(events, ZoneInfo('UTC'), ford_autocal=True)
  assert row.get('ford_candidate_low', 0) == 0  # initial/driver-grip cooldown covers low-speed portion
  assert row['ford_candidate_high'] == 30
  # A control message without a fresh carState must not inflate candidate coverage.
  events.append(event('carControl', 12, latActive=True, actuators=SimpleNamespace(curvature=0.0015)))
  assert summarize(events, ZoneInfo('UTC'), ford_autocal=True)['ford_candidate_high'] == 30
