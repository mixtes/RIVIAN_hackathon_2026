import unittest

import numpy as np

import hands
import phone_events as pe

WHEEL = [[1400, 1100], [1800, 1100], [1800, 1450], [1400, 1450]]


def kp(**pts):
    k = np.full((17, 3), np.nan, np.float32)
    k[pe.LSHO], k[pe.RSHO] = [1000, 800, .9], [1300, 800, .9]          # shoulder width 300 px
    for name, xy in pts.items():
        k[getattr(pe, name)] = [*xy, .9]
    return k


def hand_at(wrist, tips_at=None):
    """21 points: wrist point at `wrist`, fingertips at `tips_at` (default: next to the wrist)."""
    pts = np.tile(np.array(wrist, float), (21, 1))
    if tips_at is not None:
        for i in hands.TIPS:
            pts[i] = tips_at
    return dict(wrist=tuple(wrist), points=pts)


class HandOnWheelTests(unittest.TestCase):
    def test_fingertips_on_rim_count_even_with_wrist_outside(self):
        h = hand_at((1360, 1150), tips_at=(1420, 1150))                  # wrist 40 px outside, fingers on rim
        self.assertEqual(hands.hand_on_wheel(h, WHEEL, 300), hands.ON)

    def test_hand_found_away_from_wheel_is_off(self):
        self.assertEqual(hands.hand_on_wheel(hand_at((1100, 1300), (1150, 1320)), WHEEL, 300), hands.OFF)

    def test_unfound_hand_far_from_wheel_is_off_near_is_unknown(self):
        self.assertEqual(hands.hand_on_wheel(dict(wrist=(900, 1300), points=None), WHEEL, 300), hands.OFF)
        self.assertEqual(hands.hand_on_wheel(dict(wrist=(1380, 1200), points=None), WHEEL, 300), pe.UNKNOWN)


class HandsOffEvidenceTests(unittest.TestCase):
    def test_both_off_is_positive_any_on_is_negative(self):
        both_off = {'L': hand_at((1100, 1300), (1150, 1320)), 'R': hand_at((1000, 1250), (1040, 1270))}
        self.assertEqual(hands.hands_off_evidence(0, kp(), both_off, WHEEL)[0].state, pe.POSITIVE)
        one_on = {'L': hand_at((1360, 1150), (1420, 1150)), 'R': hand_at((1000, 1250), (1040, 1270))}
        self.assertEqual(hands.hands_off_evidence(0, kp(), one_on, WHEEL)[0].state, pe.NEGATIVE)

    def test_one_hidden_hand_or_no_wheel_is_unknown(self):
        one_off = {'L': hand_at((1100, 1300), (1150, 1320))}
        self.assertEqual(hands.hands_off_evidence(0, kp(), one_off, WHEEL)[0].state, pe.UNKNOWN)
        self.assertEqual(hands.hands_off_evidence(0, kp(), one_off, None)[0].state, pe.UNKNOWN)
        self.assertEqual(hands.hands_off_evidence(0, None, {}, WHEEL)[0].state, pe.UNKNOWN)


class FingersOnPhoneTests(unittest.TestCase):
    def test_counts_points_inside_padded_box(self):
        pts = np.array([[100, 100]] * 10 + [[300, 300]] * 11, float)
        self.assertEqual(hands.fingers_on_phone(pts, (90, 90, 140, 160, .8)), 10)
        self.assertEqual(hands.fingers_on_phone(None, (90, 90, 140, 160, .8)), 0)


class HandFinderTests(unittest.TestCase):
    def test_maps_crop_points_back_and_rejects_other_hands(self):
        frame = np.zeros((1536, 1920, 3), np.uint8)
        center = [(0.5, 0.5)] * 21                                         # hand exactly at the crop center
        f = hands.HandFinder(detect=lambda rgb: center)
        found = f.find(frame, kp(LWR=(1100, 1200), RWR=(1500, 1250)))
        np.testing.assert_allclose(found['L']['points'][0], (1100, 1200), atol=2)
        np.testing.assert_allclose(found['R']['points'][0], (1500, 1250), atol=2)
        corner = [(0.0, 0.0)] * 21                                         # hand far from this wrist
        found = hands.HandFinder(detect=lambda rgb: corner).find(frame, kp(LWR=(1100, 1200)))
        self.assertIsNone(found['L']['points'])
        self.assertEqual(hands.HandFinder(detect=lambda rgb: None).find(frame, None), {})


if __name__ == '__main__':
    unittest.main()
