"""Performance changes must preserve matching and bound stationary retention."""
import random
from typing import Any

from .core import Observer, Sample, match_path
from .daemon import StopMatcher
from .types import Target


def reference_match(sample: Sample, heading: float | None, stops: list[dict[str, Any]]) -> Target | None:
  matches: list[Target] = []
  for stop in stops:
    if stop['disabled'] or (not stop['visits'] and not stop['confirmed']):
      continue
    position = stop['position']
    distance = match_path(sample, position['path'], heading)
    if distance is not None and 0 < distance <= 600:
      matches.append({'id': stop['id'], 'distance': distance, 'accuracy': position['accuracy'] or 1e6,
                      'ready': stop['ready'], 'visits': stop['visits'], 'confirmed': stop['confirmed']})
  matches.sort(key=lambda match: match['distance'])
  if len(matches) > 1 and matches[1]['distance'] - matches[0]['distance'] < 25:
    return None
  return matches[0] if matches else None


def test_cached_bounds_preserve_matches_and_ambiguity() -> None:
  rng = random.Random(7)
  stops = [{'id': str(i), 'disabled': i % 7 == 0, 'visits': 3, 'confirmed': 'user', 'ready': True,
            'position': {'path': [[37 + i * .001, -122], [37.003 + i * .001, -122]], 'accuracy': .5}}
           for i in range(25)]
  # Include an overlapping approach, parallel road and opposite direction.
  stops.extend([{'id': 'ambiguous', 'disabled': False, 'visits': 3, 'confirmed': '', 'ready': False,
                 'position': {'path': [[37.001, -122], [37.0041, -122]], 'accuracy': 1}}])
  matcher = StopMatcher(stops)
  found = rejected = 0
  for _ in range(1000):
    sample = Sample(t=1, utc=1, speed=15, lat=37 + rng.uniform(0, .03), lon=-122 + rng.uniform(-.00006, .00006))
    heading = rng.choice([0., 10., 180., None])
    actual = matcher.find(sample, heading)
    assert actual == reference_match(sample, heading, stops)
    found += actual is not None
    rejected += actual is None
  assert found > 0 and rejected > 0


def test_stationary_buffers_stop_growing_after_observation() -> None:
  observer = Observer('drive')
  for i in range(100_000):
    observer.update(Sample(t=i / 100, utc=1800000000 + i / 100, speed=0))
  assert observer.emitted
  assert len(observer.stationary) <= 102
  assert len(observer.history) <= 4001


def test_localization_cache_keeps_freshness_and_motion_uncertainty() -> None:
  from types import SimpleNamespace as NS
  from .telemetry import Telemetry

  telemetry = Telemetry()
  gps = NS(unixTimestampMillis=1800000000000, hasFix=True, latitude=37., longitude=-122., bearingAccuracyDeg=1., bearingDeg=90.)
  llk = NS(gpsOK=True, inputsOK=True, sensorsOK=True, positionECEF=NS(valid=True, std=[1., 1., 1.]),
           positionGeodetic=NS(valid=True, value=[37.1, -122.1, 0.]), calibratedOrientationNED=NS(valid=True, value=[0., 0., 0.]))
  cs = NS(canValid=True, vEgo=2., gearShifter='drive', brakePressed=False, gasPressed=False)
  telemetry.messages = {'carState': (10., True, cs), 'gpsLocation': (10., True, gps), 'liveLocationKalman': (10., True, llk)}
  first = telemetry.sample()
  assert first is not None and first[0].accuracy is not None
  cached = telemetry._location
  cs.vEgo = 10.
  telemetry.messages['carState'] = (10.1, True, cs)
  later = telemetry.sample()
  assert later is not None and later[0].accuracy is not None
  assert telemetry._location is cached
  assert later[0].accuracy > first[0].accuracy + 1.
  assert later[1] == 0.  # Fresh inertial heading still overrides GPS.
  telemetry.messages['carState'] = (10.21, True, cs)
  stale = telemetry.sample()
  assert stale is not None and stale[0].accuracy is None and stale[0].lat == 37.
  assert stale[1] == 90.
  telemetry.messages['carState'] = (12.1, True, cs)
  missing = telemetry.sample()
  assert missing is not None and missing[0].lat is None and missing[1] is None


def test_batches_keep_brief_events_and_frame_indices() -> None:
  from types import SimpleNamespace as NS
  from unittest.mock import patch  # noqa: TID251 -- exercise native socket boundary
  from .daemon import message_batches

  # Inputs arrive on different service queues. Keep both sides of a short
  # brake/lead transition and every frame, in the same order as offline replay.
  events = [NS(logMonoTime=i, kind=kind) for i, kind in enumerate(
    ['carState', 'radarState', 'roadEncodeIdx', 'carState', 'radarState', 'roadEncodeIdx'])]
  queues = {kind: [event for event in events if event.kind == kind]
            for kind in ['carState', 'radarState', 'roadEncodeIdx']}
  with patch('cereal.messaging.sub_sock', side_effect=lambda name, **kwargs: name) as sockets, \
       patch('cereal.messaging.drain_sock', side_effect=lambda name, **kwargs: queues[name]), \
       patch('openpilot.common.realtime.Ratekeeper'):
    batches = message_batches(list(queues))
    assert next(batches) == events
    assert all(call.kwargs['conflate'] is False for call in sockets.call_args_list)


def test_cached_lead_and_control_expire_and_keep_short_events() -> None:
  from types import SimpleNamespace as NS
  from .telemetry import Telemetry

  telemetry = Telemetry()
  cs = NS(canValid=True, vEgo=2., gearShifter='drive', brakePressed=True, gasPressed=False)
  telemetry.messages = {'carState': (10., True, cs),
                        'carControl': (10., True, NS(longActive=False)),
                        'radarState': (10., True, NS(leadOne=NS(status=True, dRel=5.))),
                        'modelV2': (10., True, NS(action=NS(shouldStop=True)))}
  result = telemetry.sample()
  assert result is not None and result[0].lead == 5. and not result[0].long_active and result[0].model_stop
  telemetry.messages['carControl'] = (10.01, True, NS(longActive=True))
  telemetry.messages['carState'] = (10.01, True, cs)
  result = telemetry.sample()
  assert result is not None and result[0].long_active
  telemetry.messages['carControl'] = (10.02, True, NS(longActive=False))
  telemetry.messages['carState'] = (10.02, True, cs)
  result = telemetry.sample()
  assert result is not None and not result[0].long_active
  telemetry.messages['carState'] = (11.1, True, cs)
  result = telemetry.sample()
  assert result is not None and result[0].lead is None and result[0].long_active and not result[0].model_stop and not result[2]
