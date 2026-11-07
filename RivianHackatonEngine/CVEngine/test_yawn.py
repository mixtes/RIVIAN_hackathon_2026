import unittest
from types import SimpleNamespace

import numpy as np

import cv_flags
import yawn

LIPS = (500.0, 400.0)
FACE_W = 200.0
ON_LIPS = [np.array([[505.0, 405.0], [520.0, 410.0]])]                  # 0.03 face widths from the lips
AWAY = [np.array([[900.0, 900.0]])]


def face(jaw):
    return SimpleNamespace(jaw_open=jaw, lips=LIPS, face_w=FACE_W)


def run(frames, step=0.27):
    """frames: list of (jaw or None for no face, hand points) -> list of states."""
    det = yawn.YawnDetector()
    return [det.update(i * step, None if jaw is None else face(jaw), lambda h=h: h).state
            for i, (jaw, h) in enumerate(frames)]


class YawnTests(unittest.TestCase):
    def test_mouth_opens_then_hand_covers_is_a_yawn(self):
        states = run([(0.0, AWAY)] * 3 + [(0.35, AWAY), (0.4, AWAY), (0.0, ON_LIPS), (None, ON_LIPS),
                                         (None, ON_LIPS), (0.0, AWAY), (0.0, AWAY), (0.0, AWAY), (0.0, AWAY)])
        self.assertEqual(states[:5], ['none'] * 5)
        self.assertEqual(states[5:8], ['yawn'] * 3)                          # face hidden: last lips reused
        self.assertEqual(states[-1], 'none')                                 # ends after the hand leaves

    def test_hand_at_mouth_without_open_mouth_is_not_a_yawn(self):          # chin rest, smoking, coughing
        self.assertNotIn('yawn', run([(0.0, ON_LIPS)] * 20))

    def test_eating_order_is_not_a_yawn(self):                              # hand with food first, then mouth opens
        self.assertNotIn('yawn', run([(0.0, ON_LIPS)] * 3 + [(0.5, ON_LIPS), (0.5, ON_LIPS), (0.4, AWAY),
                                                              (0.0, AWAY), (0.0, AWAY)]))

    def test_single_open_frame_then_hand_is_not_a_yawn(self):               # talking / one bite
        self.assertNotIn('yawn', run([(0.0, AWAY), (0.45, AWAY), (0.0, AWAY), (0.0, ON_LIPS), (0.0, ON_LIPS)]))

    def test_hand_too_late_is_not_a_yawn(self):
        self.assertNotIn('yawn', run([(0.35, AWAY), (0.35, AWAY)] + [(0.0, AWAY)] * 8 + [(0.0, ON_LIPS)] * 3))

    def test_long_uncovered_yawn(self):
        states = run([(0.5, AWAY)] * 8)                                      # 1.9 s wide open, no hand
        self.assertEqual(states[-1], 'yawn')
        self.assertEqual(states[0], 'none')

    def test_hands_are_only_searched_when_needed(self):
        calls = []
        det = yawn.YawnDetector()
        for i in range(10):
            det.update(i * 0.27, face(0.0), lambda: calls.append(1) or AWAY)
        self.assertEqual(calls, [])

    def test_time_going_back_resets(self):
        det = yawn.YawnDetector()
        for i, (jaw, h) in enumerate([(0.4, AWAY), (0.4, AWAY), (0.0, ON_LIPS)]):
            det.update(10 + i * 0.27, face(jaw), lambda h=h: h)
        self.assertIsNotNone(det.active_since)
        self.assertEqual(det.update(1.0, face(0.0), lambda: AWAY).state, 'none')

    def test_flag_bit(self):
        self.assertEqual(cv_flags.pack_flags(yawn=True), 0x08)
        self.assertEqual(cv_flags.pack_flags(phone=True, yawn=True), 0x18)

    def test_hand_points_from_hand_finder_output(self):
        pts = yawn.hand_points({'L': dict(wrist=(0, 0), points=None), 'R': dict(wrist=(1, 1), points=np.zeros((21, 2)))})
        self.assertEqual(len(pts), 1)


if __name__ == '__main__':
    unittest.main()
