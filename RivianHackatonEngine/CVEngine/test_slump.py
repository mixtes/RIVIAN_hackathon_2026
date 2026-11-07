import unittest

import numpy as np

from slump import SlumpDetector, head_shoulder_ratio

DT = 0.14


def pose(head_dy, sw=320.0, conf=1.0):
    """17x3 keypoints: shoulders at y=800, head `head_dy` px above them (negative = below)."""
    kp = np.zeros((17, 3))
    kp[5] = (1000 - sw / 2, 800, conf)
    kp[6] = (1000 + sw / 2, 800, conf)
    for i in range(5):
        kp[i] = (1000, 800 - head_dy, conf)
    return kp


def run(det, frames, t0=0.0):
    out, t = [], t0
    for f in frames:
        out.append(det.update(t, f))
        t += DT
    return out


class SlumpTests(unittest.TestCase):
    def warm(self):
        d = SlumpDetector()
        run(d, [pose(180)] * 40)
        return d

    def test_ratio(self):
        self.assertAlmostEqual(head_shoulder_ratio(pose(160, 320)), 0.5)
        self.assertLess(head_shoulder_ratio(pose(-100)), 0)
        self.assertIsNone(head_shoulder_ratio(None))
        self.assertIsNone(head_shoulder_ratio(pose(160, conf=0.1)))

    def test_upright_driver_stays_upright(self):
        out = run(self.warm(), [pose(170)] * 20, 6.0)
        self.assertTrue(all(o.state == 'upright' for o in out))

    def test_collapse_detected_after_min_duration(self):
        d = self.warm()
        out = run(d, [pose(-90, 250)] * 20, 6.0)
        self.assertEqual(out[2].state, 'upright')          # not yet: filters a momentary reach
        self.assertEqual(out[-1].state, 'slumped')
        self.assertGreater(out[-1].slumped_s, 1.5)

    def test_forward_lean_with_head_above_shoulders_is_not_a_slump(self):
        d = self.warm()
        out = run(d, [pose(35, 250)] * 30, 6.0)             # head dropped a lot, but still above the shoulder line
        self.assertTrue(all(o.state == 'upright' for o in out))

    def test_short_reach_is_ignored(self):
        d = self.warm()
        out = run(d, [pose(-60)] * 4 + [pose(180)] * 10, 6.0)   # 0.56 s down
        self.assertTrue(all(o.state == 'upright' for o in out))

    def test_driver_lost_while_slumped_stays_slumped_then_unknown(self):
        d = self.warm()
        run(d, [pose(-90, 250)] * 12, 6.0)                  # confirmed slumped
        out = run(d, [None] * 40, 6.0 + 12 * DT)
        self.assertEqual(out[3].state, 'slumped')
        self.assertTrue(out[3].held)
        self.assertEqual(out[-1].state, 'unknown')          # bit 0: never held on without observations

    def test_driver_lost_while_upright_is_unknown_not_slumped(self):
        d = self.warm()
        out = run(d, [None] * 12, 6.0)
        self.assertNotIn('slumped', [o.state for o in out])
        self.assertEqual(out[-1].state, 'unknown')

    def test_recovery(self):
        d = self.warm()
        run(d, [pose(-90, 250)] * 15, 6.0)
        out = run(d, [pose(175)] * 15, 8.5)
        self.assertEqual(out[-1].state, 'upright')

    def test_adapts_to_low_seated_driver(self):
        d = SlumpDetector()
        run(d, [pose(110)] * 60)                             # sits low: head only 0.34 shoulder-widths up
        out = run(d, [pose(110)] * 20, 9.0)
        self.assertTrue(all(o.state == 'upright' for o in out))


if __name__ == '__main__':
    unittest.main()
