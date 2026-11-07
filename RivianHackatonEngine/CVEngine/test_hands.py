"""Tests for the three hand methods (pose / mediapipe / hybrid) and the live state machine. No model inference."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import hands_hybrid, hands_mediapipe, hands_pose, live, rules

WHEEL = np.array([[100, 100], [300, 100], [300, 300], [100, 300]], np.int32)


def keypoints(left=None, right=None):
    """17x3 pose array; left/right are (wrist_xy, elbow_xy) or None (hidden)."""
    kp = np.zeros((17, 3), np.float32)
    for (wrist, elbow), spec in ((( rules.LWR, rules.LELB), left), ((rules.RWR, rules.RELB), right)):
        if spec is not None:
            kp[wrist] = [*spec[0], 0.9]
            kp[elbow] = [*spec[1], 0.9]
    return kp


class PoseHandsTests(unittest.TestCase):
    def test_near_away_unknown(self):
        kp = keypoints(left=((200, 200), (200, 150)), right=((600, 600), (600, 550)))
        states, _ = hands_pose.PoseHands(WHEEL, 0).update(None, kp)
        self.assertEqual(states, ['near', 'away'])
        self.assertEqual(hands_pose.PoseHands(WHEEL, 0).update(None, keypoints())[0], ['unknown', 'unknown'])
        self.assertEqual(hands_pose.PoseHands(WHEEL, 0).update(None, None)[0], ['unknown', 'unknown'])

    def test_hand_point_extends_along_forearm(self):
        (wrist, hand), _ = hands_pose.hand_points(keypoints(left=((90, 200), (90, 100))))[0], None
        self.assertAlmostEqual(float(hand[1] - wrist[1]), rules.HAND_EXT * 100, places=3)


class CombineTests(unittest.TestCase):
    def test_rules(self):
        c = hands_hybrid.combine
        self.assertEqual(c('near', 'away'), ('near', 'mediapipe'))       # MediaPipe overrides the proxy
        self.assertEqual(c('away', 'near'), ('away', 'mediapipe'))
        self.assertEqual(c('unknown', 'near'), ('near', 'pose'))         # proxy fills a gap with 'near'
        self.assertEqual(c('unknown', 'away'), ('unknown', 'none'))      # proxy 'away' is never trusted alone
        self.assertEqual(c('unknown', 'unknown'), ('unknown', 'none'))

    def test_hybrid_uses_both_methods_per_hand(self):
        pose = Mock(); pose.update.return_value = (['near', 'away'], dict(points=['p0', 'p1'], landmarks=[None, None]))
        mp = Mock(); mp.update.return_value = (['unknown', 'unknown'], dict(points=[], landmarks=['l0', None]))
        states, dbg = hands_hybrid.HybridHands(WHEEL, 0, pose=pose, mediapipe=mp).update(None, None)
        self.assertEqual(states, ['near', 'unknown'])
        self.assertEqual(dbg['sources'], ['pose', 'none'])


class MediaPipeHandsTests(unittest.TestCase):
    FRAME = np.zeros((600, 800, 3), np.uint8)

    def landmarks(self, centre, spread=20, n=21):
        """normalised landmarks (relative to the 400px window) of a hand centred at `centre` (full-frame px)."""
        pts = np.random.RandomState(0).uniform(-spread, spread, (n, 2)) + centre
        return [SimpleNamespace(x=(x - self.x0) / 400, y=(y - self.y0) / 400) for x, y in pts]

    def run_method(self, hand_centre, wrist_hand_centre=(200, 200)):
        kp = keypoints(left=((wrist_hand_centre[0], wrist_hand_centre[1]), (wrist_hand_centre[0], wrist_hand_centre[1])))
        method = hands_mediapipe.MediaPipeHands(WHEEL, landmarker=Mock())
        self.x0 = int(min(max(wrist_hand_centre[0] - 200, 0), 800 - 400))
        self.y0 = int(min(max(wrist_hand_centre[1] - 200, 0), 600 - 400))
        found = [] if hand_centre is None else [self.landmarks(hand_centre)]
        with patch.object(method, '_detect', return_value=found):
            return method.update(self.FRAME, kp)

    def test_landmarks_on_wheel_are_near(self):
        states, dbg = self.run_method((200, 200))
        self.assertEqual(states[0], 'near')
        self.assertEqual(dbg['sources'][0], 'mediapipe')
        self.assertGreaterEqual(int(dbg['landmarks'][0][1].sum()), hands_mediapipe.MIN_IN)

    def test_found_hand_away_from_wheel_is_away(self):
        self.assertEqual(self.run_method((500, 500), wrist_hand_centre=(500, 500))[0][0], 'away')

    def test_no_hand_found_is_unknown_not_away(self):
        states, dbg = self.run_method(None)
        self.assertEqual(states[0], 'unknown')
        self.assertEqual(dbg['sources'][0], 'none')

    def test_hidden_wrist_makes_no_window(self):
        method = hands_mediapipe.MediaPipeHands(WHEEL, landmarker=Mock())
        with patch.object(method, '_detect') as detect:
            self.assertEqual(method.update(self.FRAME, keypoints())[0], ['unknown', 'unknown'])
        detect.assert_not_called()


class LiveLogicTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(live.classify(['near', 'unknown']), 'near')
        self.assertEqual(live.classify(['away', 'away']), 'off')
        self.assertEqual(live.classify(['away', 'unknown']), 'unknown')   # one hidden hand is not evidence

    def test_hands_off_flag(self):
        self.assertEqual(live.hands_off_flag('off'), (1, 0))        # both hands seen, none on the wheel
        self.assertEqual(live.hands_off_flag('near'), (0, 0))       # at least one hand on the wheel
        self.assertEqual(live.hands_off_flag('unknown'), (0, 1))    # cannot tell: not reported as hands off

    def test_event_confirmed_after_hold_and_cleared_by_near(self):
        m, events = live.HandsOffMachine(), []
        for i in range(40):                       # 0..3.9 s of 'off' sampled at 10 Hz
            events += m.update(i / 10, 'off')
        self.assertEqual([e['type'] for e in events], ['confirmed'])
        self.assertAlmostEqual(events[0]['onset_t'], 0.0)
        self.assertAlmostEqual(events[0]['confirmed_t'], live.HOLD_S, places=1)
        for i in range(40, 50):
            events += m.update(i / 10, 'near')
        self.assertEqual(events[-1]['type'], 'ended')
        self.assertEqual(events[-1]['reason'], 'cleared')

    def test_unknown_gap_longer_than_loss_ends_event_as_observation_lost(self):
        m = live.HandsOffMachine()
        for i in range(15):
            m.update(i / 10, 'off')
        events = m.update(1.4 + live.LOSS_S + 0.5, 'unknown')
        self.assertEqual(events[0]['reason'], 'observation_lost')

    def test_short_burst_does_not_confirm(self):
        m, events = live.HandsOffMachine(), []
        for i in range(5):
            events += m.update(i / 10, 'off')
        for i in range(5, 10):
            events += m.update(i / 10, 'near')
        for i in range(10, 15):
            events += m.update(i / 10, 'off')
        self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main()
