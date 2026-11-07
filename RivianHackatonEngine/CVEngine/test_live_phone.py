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


if __name__ == '__main__':
    unittest.main()
