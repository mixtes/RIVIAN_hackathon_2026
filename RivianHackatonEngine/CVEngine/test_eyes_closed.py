import unittest

from eyes_closed import EyeClosureDetector

DT = 0.14


def run(det, values, t0=0.0):
    out, t = [], t0
    for v in values:
        if isinstance(v, dict):
            out.append(det.update(t, v.get('blink'), v.get('gaze', 0.0), v.get('ear'), v.get('pitch')))
        else:
            out.append(det.update(t, v[0] if isinstance(v, tuple) else v, v[1] if isinstance(v, tuple) else 0.0))
        t += DT
    return out


class EyeClosureTests(unittest.TestCase):
    def warm(self):
        d = EyeClosureDetector()
        run(d, [0.1] * 40)
        return d

    def test_blink_is_ignored(self):
        d = self.warm()
        out = run(d, [0.9, 0.9, 0.1, 0.1, 0.1], 6.0)
        self.assertTrue(all(o.state == 'open' for o in out))

    def test_long_closure_detected_and_released(self):
        d = self.warm()
        out = run(d, [0.9] * 10 + [0.1] * 5, 6.0)
        self.assertEqual(out[1].state, 'open')
        self.assertEqual(out[6].state, 'closed')
        self.assertGreater(out[9].closed_s, 0.8)
        self.assertEqual(out[-1].state, 'open')

    def test_single_dropped_frame_inside_closure(self):
        d = self.warm()
        out = run(d, [0.9, 0.9, None, 0.9, 0.9, 0.9, 0.9], 6.0)
        self.assertEqual(out[-1].state, 'closed')

    def test_looking_down_is_not_closed(self):
        d = self.warm()
        out = run(d, [(0.55, 0.9)] * 12, 6.0)
        self.assertTrue(all(o.state == 'open' for o in out))

    def test_no_face_becomes_unknown_and_bit_none(self):
        d = self.warm()
        out = run(d, [None] * 8, 6.0)
        self.assertEqual(out[-1].state, 'unknown')
        self.assertIsNone(d.raw_bit(out[-1]))

    def test_adapts_to_driver_with_high_open_level(self):
        d = EyeClosureDetector()
        run(d, [0.35] * 60)
        out = run(d, [0.5] * 15, 9.0)     # slightly more than usual: not closed
        self.assertTrue(all(o.state != 'closed' for o in out))

    def test_ear_confirms_closure_when_blink_is_weak(self):
        d = EyeClosureDetector()
        run(d, [{'blink': 0.15, 'ear': 0.30}] * 40)
        out = run(d, [{'blink': 0.40, 'ear': 0.06}] * 10, 6.0)     # blendshape alone is borderline, EAR is clear
        self.assertEqual(out[-1].state, 'closed')

    def test_noisy_blink_without_ear_support_stays_open(self):
        d = EyeClosureDetector()
        run(d, [{'blink': 0.15, 'ear': 0.30}] * 40)
        out = run(d, [{'blink': 0.42, 'ear': 0.29}] * 12, 6.0)     # squint-like blink score, eyes really open
        self.assertTrue(all(o.state == 'open' for o in out))

    def test_looking_down_with_low_ear_is_not_closed(self):
        d = EyeClosureDetector()
        run(d, [{'blink': 0.15, 'ear': 0.30}] * 40)
        out = run(d, [{'blink': 0.5, 'ear': 0.21, 'gaze': 0.9}] * 12, 6.0)
        self.assertTrue(all(o.state == 'open' for o in out))

    def test_head_down_closure_look_is_not_closed(self):
        d = EyeClosureDetector()
        run(d, [{'blink': 0.15, 'ear': 0.30}] * 40)
        out = run(d, [{'blink': 0.6, 'ear': 0.08, 'pitch': 15.0}] * 15, 6.0)   # reading a phone in the lap
        self.assertNotIn('closed', [o.state for o in out])
        out = run(d, [{'blink': 0.6, 'ear': 0.08, 'pitch': 2.0}] * 10, 9.0)     # same eyes, head up: closed
        self.assertEqual(out[-1].state, 'closed')

    def test_ear_only_without_blendshape(self):
        d = EyeClosureDetector()
        run(d, [{'ear': 0.30}] * 40)
        out = run(d, [{'ear': 0.05}] * 10, 6.0)
        self.assertEqual(out[-1].state, 'closed')

    def test_repeated_microsleeps_make_drowsy(self):
        d = EyeClosureDetector()
        t = 0.0
        for _ in range(4):
            run(d, [0.1] * 40, t); t += 40 * DT
            run(d, [0.9] * 8, t); t += 8 * DT           # 1.1 s closures
        out = run(d, [0.1] * 3, t)
        self.assertGreaterEqual(out[-1].microsleeps, 3)
        self.assertTrue(out[-1].drowsy)

    def test_alert_driver_not_drowsy(self):
        d = EyeClosureDetector()
        t = 0.0
        for _ in range(8):
            run(d, [0.1] * 40, t); t += 40 * DT
            run(d, [0.9] * 2, t); t += 2 * DT           # ordinary blinks
        out = run(d, [0.1] * 3, t)
        self.assertFalse(out[-1].drowsy)
        self.assertEqual(out[-1].microsleeps, 0)


if __name__ == '__main__':
    unittest.main()
