import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

import live_phone as lp


def keypoints(**points):
    kp = np.full((17, 3), np.nan, np.float32)
    kp[lp.LSHO] = [1000, 800, .9]
    kp[lp.RSHO] = [1300, 800, .9]            # shoulder width 300 px
    for name, xy in points.items():
        kp[getattr(lp, name)] = [*xy, .9]
    return kp


def phone_at(x, y, conf=0.4):
    return (x - 30, y - 50, x + 30, y + 50, conf)


class PhoneFlagTests(unittest.TestCase):
    def test_is_dark_uses_crop_brightness(self):
        self.assertTrue(lp.is_dark(np.full((40, 40, 3), 30, np.uint8)))
        self.assertFalse(lp.is_dark(np.full((40, 40, 3), 90, np.uint8)))

    def test_hand_near_ear_without_phone_is_not_phone_at_ear(self):
        flags = lp.phone_flags(keypoints(LEAR=(1100, 600), LWR=(1110, 610)), [])
        self.assertEqual(flags, dict(phone=False, conf=0.0, at_ear=False, in_hand=False))

    def test_phone_near_ear(self):
        flags = lp.phone_flags(keypoints(LEAR=(1100, 600), LWR=(1110, 650)), [phone_at(1120, 620)])
        self.assertTrue(flags['phone'] and flags['at_ear'])
        self.assertFalse(flags['in_hand'])

    def test_phone_in_hand_low(self):
        flags = lp.phone_flags(keypoints(LEAR=(1100, 600), LWR=(1150, 1200)), [phone_at(1160, 1220)])
        self.assertTrue(flags['in_hand'])
        self.assertFalse(flags['at_ear'])

    def test_phone_far_from_driver_is_only_a_detection(self):
        flags = lp.phone_flags(keypoints(LEAR=(1100, 600), LWR=(1150, 1200)), [phone_at(1800, 1450, .7)])
        self.assertTrue(flags['phone'])
        self.assertAlmostEqual(flags['conf'], .7)
        self.assertFalse(flags['at_ear'] or flags['in_hand'])

    def test_no_driver_keeps_raw_detection(self):
        flags = lp.phone_flags(None, [phone_at(1200, 900)])
        self.assertTrue(flags['phone'])
        self.assertFalse(flags['at_ear'] or flags['in_hand'])


class DriverSelectionTests(unittest.TestCase):
    def test_picks_largest_person_on_driver_side(self):
        boxes = np.array([[100, 100, 900, 900],      # passenger side, largest overall
                          [1000, 300, 1300, 900],    # driver side, small
                          [1000, 200, 1800, 1400]])  # driver side, largest
        kps = np.arange(3)[:, None, None] * np.ones((3, 17, 3))
        self.assertEqual(lp.select_driver(boxes, kps, 1920)[0, 0], 2)
        self.assertIsNone(lp.select_driver(boxes[:1], kps[:1], 1920))


class LatestFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.video = str(Path(cls.temp.name) / 'clip.mp4')
        writer = cv2.VideoWriter(cls.video, cv2.VideoWriter_fourcc(*'mp4v'), 30, (64, 48))
        for i in range(30):
            writer.write(np.full((48, 64, 3), i * 8, np.uint8))
        writer.release()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def consume(self, realtime, delay):
        reader = lp.LatestFrame(self.video, realtime=realtime).start()
        seen, last = [], None
        try:
            while (item := reader.get(last)) is not None:
                last = item[0]
                seen.append(item[0])
                time.sleep(delay)
        finally:
            reader.stop()
        return seen, reader.read_count

    def test_all_frames_mode_delivers_every_frame_in_order(self):
        seen, read = self.consume(realtime=False, delay=0.01)
        self.assertEqual(seen, list(range(30)))
        self.assertEqual(read, 30)

    def test_live_mode_skips_frames_when_processing_is_slow(self):
        seen, read = self.consume(realtime=True, delay=0.2)
        self.assertEqual(read, 30)
        self.assertGreaterEqual(len(seen), 3)
        self.assertLess(len(seen), 15)
        self.assertEqual(seen, sorted(set(seen)))


class PhoneMonitorTests(unittest.TestCase):
    """PhoneMonitor with a fake detector: a phone moving in the driver's hand switches the bit on after
    confirmation and off again ~3 s after the phone is gone."""

    def make(self, boxes_at):
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeYolo:
            names = {67: 'cell phone'}

            def __init__(self, *_):
                pass

            def __call__(self, crop, **_):
                b = boxes_at[0]
                xyxy = np.array([[b[0] - lp.CX0, b[1] - lp.CY0, b[2] - lp.CX0, b[3] - lp.CY0]] if b else np.zeros((0, 4)))
                conf = np.array([b[4]] if b else [])
                tensor = lambda a: SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: a))
                return [SimpleNamespace(boxes=SimpleNamespace(xyxy=tensor(xyxy), conf=tensor(conf)))]

        with patch('ultralytics.YOLO', FakeYolo):
            return lp.PhoneMonitor(dark_phone_model=None, hands=False)

    def test_bit_follows_confirmed_phone_use(self):
        current = [None]
        mon = self.make(current)
        frame = np.zeros((1536, 1920, 3), np.uint8)
        bits = []
        for i in range(40):
            t = i / 3
            if i < 15:
                x, y = 1050 + 12 * i, 1300 - 6 * i                               # phone moves with the hand
                current[0] = phone_at(x, y)
                kp = keypoints(RWR=(x, y - 60))
            else:
                current[0], kp = None, keypoints(RWR=(1500, 1100))               # phone gone
            bits.append(mon.update(t, frame, kp))
        self.assertEqual(bits[0], 0)                                               # not confirmed yet
        self.assertIn(1, bits[:15])
        on = bits.index(1)
        self.assertGreaterEqual(on, 3)
        self.assertEqual(bits[-1], 0)
        off = len(bits) - bits[::-1].index(1)
        self.assertGreaterEqual((off - 15) / 3, 2.5)                               # ~3 s tail after use ends

    def test_handling_covers_recent_use_and_resets_on_seek(self):
        current = [None]
        mon = self.make(current)
        frame = np.zeros((1536, 1920, 3), np.uint8)
        self.assertFalse(mon.handling(0.0))
        for i in range(9):                                                         # moving phone in the hand
            x, y = 1050 + 12 * i, 1300 - 6 * i
            current[0] = phone_at(x, y)
            mon.update(i / 3, frame, keypoints(RWR=(x, y - 60)))
        self.assertIsNotNone(mon.last_positive_t)
        self.assertTrue(mon.handling(8 / 3))
        self.assertTrue(mon.handling(mon.last_positive_t + 2.9))                   # within 3 s of the last positive
        mon.handling(0.0)                                                          # time went back: forget
        self.assertIsNone(mon.last_positive_t)


if __name__ == '__main__':
    unittest.main()
