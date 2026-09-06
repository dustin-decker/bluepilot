from collections import Counter

from opendbc.sunnypilot.car.ford.angle_autocal_controller import AutoCalController
from opendbc.sunnypilot.car.ford.tests.replay_angle_autocal import observe_feed
from opendbc.sunnypilot.car.ford.tests.test_angle_autocal import _MockParams
from opendbc.sunnypilot.car.ford.tests.test_gain_retune import frame, gain_model


def test_observer_does_not_change_admission_or_persistence():
  controllers = []
  params = []
  for _ in range(2):
    p = _MockParams({'FordAngleAutoCal': True, 'FordAngleAutoCalLock': False})
    ctl = AutoCalController(0.05)
    ctl.poll_params(p, 1, 1, 1.05)
    controllers.append(ctl)
    params.append(p)
  counts = Counter()
  observe_feed(controllers[1], counts)
  for _ in range(600):
    evidence = frame(20, 0.003, 0.003, gain_model(20, 0.003, 1, 1))
    for ctl in controllers:
      ctl.feed(evidence, True)
  assert controllers[0].pipeline.to_dict() == controllers[1].pipeline.to_dict()
  assert params[0].values == params[1].values
  assert counts['feed_frames'] == 600
  assert counts['accepted_low'] == controllers[1].pipeline.est.n > 0
  assert counts['accepted_positive'] == counts['accepted_low']
