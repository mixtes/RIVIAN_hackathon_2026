import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import cv2

import auto_wheel
import rules


class WheelCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.cache = str(self.out / 'cache.npz')
        self.polygon = [[10, 10], [100, 10], [100, 100], [10, 100]]
        self.write_calibration()

    def write_calibration(self, **changes):
        data = dict(wheel=self.polygon, frame_size=[200, 200], source='auto_wheel')
        data.update(changes)
        (self.out / 'wheel.json').write_text(json.dumps(data))

    def make_cache(self):
        pose = np.full((40, 2, 17, 3), np.nan, np.float32)
        pose[:, 1, rules.LWR] = [50, 50, .9]
        pose[:, 1, rules.RWR] = [60, 60, .9]
        np.savez(self.cache, P=pose, PH=np.full((40, 3, 5), np.nan),
                 done=np.ones(40, bool))

    def test_loads_saved_polygon_without_double_margin(self):
        wheel, margin = rules.load_wheel(self.cache, (200, 200))
        np.testing.assert_array_equal(wheel, self.polygon)
        self.assertEqual(margin, 0)
        self.assertTrue(rules.on_wheel((50, 50), wheel, margin))
        self.assertFalse(rules.on_wheel((110, 50), wheel, margin))

    def test_manual_zone_preserves_margin(self):
        self.write_calibration(source='manual')
        wheel, margin = rules.load_wheel(self.cache)
        self.assertEqual(margin, 60)
        self.assertTrue(rules.on_wheel((110, 50), wheel, margin))

    def test_explicit_wrist_tolerance_is_used_without_extra_margin(self):
        self.write_calibration(wrist_margin_px=5)
        wheel, margin = rules.load_wheel(self.cache)
        self.assertEqual(margin, 5)
        self.assertTrue(rules.on_wheel((104, 50), wheel, margin))
        self.assertFalse(rules.on_wheel((106, 50), wheel, margin))

    def test_missing_calibration_fails(self):
        (self.out / 'wheel.json').unlink()
        with self.assertRaisesRegex(FileNotFoundError, 'Missing wheel calibration'):
            rules.load_wheel(self.cache)

    def test_mismatched_resolution_fails(self):
        with self.assertRaisesRegex(ValueError, 'do not match'):
            rules.load_wheel(self.cache, (400, 200))

    def test_invalid_calibration_fails(self):
        for changes in (
                dict(wheel=[]),
                dict(wheel=[[1, 1], [2, 2], [3, 3]]),
                dict(wheel=[[1, 1], [201, 2], [3, 3]]),
                dict(wheel=[['x', 1], [2, 3], [4, 5]]),
                dict(frame_size=[0, 200]),
                dict(frame_size=['200', '200']),
                dict(wrist_margin_px=-1),
                dict(wrist_margin_px=1.5),
                dict(wrist_margin_px='60'),
                dict(wrist_margin_px=201)):
            with self.subTest(changes=changes):
                self.write_calibration(**changes)
                with self.assertRaises(ValueError):
                    rules.load_wheel(self.cache)

    def test_rules_use_calibration_and_save_it_with_flags(self):
        self.make_cache()
        with contextlib.redirect_stdout(io.StringIO()):
            rules.main(self.cache)
        with np.load(self.out / 'flags.npz') as flags:
            np.testing.assert_array_equal(flags['nh'], np.full(40, 2))
            np.testing.assert_array_equal(flags['wheel'], self.polygon)
            self.assertEqual(flags['wheel_margin'].item(), 0)
        self.write_calibration(wheel=[[110, 110], [190, 110], [190, 190], [110, 190]])
        with contextlib.redirect_stdout(io.StringIO()):
            rules.main(self.cache)
        with np.load(self.out / 'flags.npz') as flags:
            np.testing.assert_array_equal(flags['nh'], np.zeros(40))
            self.assertTrue(flags['HANDS_OFF_WHEEL'].all())

    def test_render_uses_same_polygon(self):
        import cv2

        self.make_cache()
        with contextlib.redirect_stdout(io.StringIO()):
            rules.main(self.cache)
        video = self.out / 'input.mp4'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 30, (200, 200))
        self.assertTrue(writer.isOpened())
        writer.write(np.zeros((200, 200, 3), np.uint8))
        writer.release()
        drawn = []
        original = cv2.polylines

        def capture_polygon(frame, points, *args, **kwargs):
            drawn.append(points[0].copy())
            return original(frame, points, *args, **kwargs)

        with patch.object(sys, 'argv', ['render.py', str(video), self.cache]), \
                patch.object(cv2, 'polylines', side_effect=capture_polygon), \
                patch('subprocess.run', side_effect=FileNotFoundError), \
                contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(Path(__file__).with_name('render.py')), run_name='__main__')
        self.assertEqual(len(drawn), 1)
        np.testing.assert_array_equal(drawn[0], self.polygon)
        self.assertTrue((self.out / 'annotated_raw.mp4').is_file())
        self.write_calibration(source='manual')
        with patch.object(sys, 'argv', ['render.py', str(video), self.cache]), \
                self.assertRaisesRegex(SystemExit, 'Run rules.py again'):
            runpy.run_path(str(Path(__file__).with_name('render.py')), run_name='__main__')


class WheelConsensusTests(unittest.TestCase):
    def test_boxes_without_masks_never_become_a_wheel_zone(self):
        capture = Mock()
        capture.isOpened.return_value = True
        capture.get.side_effect = lambda prop: {
            cv2.CAP_PROP_FRAME_COUNT: 3,
            cv2.CAP_PROP_FRAME_WIDTH: 200,
            cv2.CAP_PROP_FRAME_HEIGHT: 200,
        }[prop]
        capture.read.return_value = (True, np.zeros((200, 200, 3), np.uint8))
        model = Mock()
        model.predict.return_value = [SimpleNamespace(masks=None, boxes=[Mock()])]
        fake_ultralytics = SimpleNamespace(YOLOE=Mock(return_value=model))
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory) / 'wheel.json')
            with patch.dict(sys.modules, {'ultralytics': fake_ultralytics}), \
                    patch.object(cv2, 'VideoCapture', return_value=capture), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(ValueError, 'Need at least'):
                auto_wheel.main('input.mp4', target)
            self.assertFalse(Path(target).exists())
        self.assertEqual(model.predict.call_count, 3)
        capture.release.assert_called_once()

    def test_majority_rejects_transient_upper_left_extension(self):
        masks = []
        for i in range(5):
            mask = np.zeros((200, 200), np.uint8)
            mask[60:160, 80:170] = 1
            if i < 2:
                mask[20:100, 20:100] = 1
            masks.append(mask)
        polygon = auto_wheel.consensus_polygon(masks)
        self.assertTrue(rules.on_wheel((100, 100), polygon, 0))
        self.assertFalse(rules.on_wheel((40, 40), polygon, 0))
        self.assertFalse(rules.on_wheel((79, 100), polygon, 0))
        self.assertLessEqual(cv2.contourArea(polygon), 100 * 90)

    def test_preserves_concavity_instead_of_filling_convex_hull(self):
        mask = np.zeros((200, 200), np.uint8)
        mask[20:160, 100:160] = 1
        mask[100:160, 20:160] = 1
        polygon = auto_wheel.consensus_polygon([mask] * 3)
        self.assertFalse(rules.on_wheel((80, 80), polygon, 0))
        self.assertTrue(rules.on_wheel((120, 80), polygon, 0))

    def test_half_the_samples_is_not_a_majority(self):
        masks = []
        for i in range(4):
            mask = np.zeros((100, 100), np.uint8)
            mask[50:80, 50:80] = 1
            if i < 2:
                mask[10:60, 10:60] = 1
            masks.append(mask)
        polygon = auto_wheel.consensus_polygon(masks)
        self.assertFalse(rules.on_wheel((20, 20), polygon, 0))

    def test_rejects_insufficient_or_disagreeing_masks(self):
        with self.assertRaisesRegex(ValueError, 'at least'):
            auto_wheel.consensus_polygon([np.ones((10, 10), np.uint8)])
        masks = []
        for i in range(3):
            mask = np.zeros((100, 100), np.uint8)
            mask[i*30:i*30+10, i*30:i*30+10] = 1
            masks.append(mask)
        with self.assertRaisesRegex(ValueError, 'no majority agreement'):
            auto_wheel.consensus_polygon(masks)
        with self.assertRaisesRegex(ValueError, 'matching dimensions'):
            auto_wheel.consensus_polygon([masks[0], masks[1], np.zeros((20, 20), np.uint8)])

    def test_filters_low_confidence_and_spatial_outliers(self):
        mask = np.zeros((200, 200), np.uint8)
        mask[60:160, 80:170] = 1
        candidates = [(.5, mask, [80, 60, 170, 160])] * 3
        candidates += [(.1, np.ones_like(mask), [0, 0, 200, 200]),
                       (.6, np.ones_like(mask), [0, 0, 30, 30])]
        polygon, used = auto_wheel.wheel_polygon(candidates)
        self.assertEqual(len(used), 3)
        self.assertFalse(rules.on_wheel((20, 20), polygon, 0))
        with self.assertRaisesRegex(ValueError, 'confidence'):
            auto_wheel.wheel_polygon([(.1, mask, [80, 60, 170, 160])] * 3)

    def test_consensus_excludes_two_off_wheel_wrists(self):
        mask = np.zeros((200, 200), np.uint8)
        mask[100:180, 100:180] = 1
        polygon = auto_wheel.consensus_polygon([mask] * 3)
        kp = np.full((17, 3), np.nan)
        kp[rules.LWR] = [50, 60, .9]
        kp[rules.RWR] = [70, 80, .9]
        margin = int(round(auto_wheel.WRIST_MARGIN_FRAC * min(np.ptp(polygon, axis=0))))
        flags = rules.frame_flags(kp, np.full((3, 5), np.nan), polygon, margin)
        self.assertEqual(flags['n_hands_on_wheel'], 0)
        self.assertTrue(flags['HANDS_OFF_WHEEL'])

    def test_tolerance_is_visible_and_bounded(self):
        polygon = np.array([[60, 60], [100, 60], [100, 100], [60, 100]], np.int32)
        outline = auto_wheel.tolerance_outline(polygon, 6, (200, 200))
        self.assertEqual(len(outline), 1)
        self.assertEqual(cv2.boundingRect(outline[0]), (54, 54, 53, 53))
        self.assertEqual(auto_wheel.tolerance_outline(polygon, 0, (200, 200)), [])


if __name__ == '__main__':
    unittest.main()
