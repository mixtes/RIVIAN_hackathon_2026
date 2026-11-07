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


class WheelRoiTests(unittest.TestCase):
    SIZE = (1920, 1536)

    def candidates(self, polygon, n=4, conf=0.1):
        mask = np.zeros((self.SIZE[1], self.SIZE[0]), np.uint8)
        cv2.fillPoly(mask, [polygon], 1)
        x, y, w, h = cv2.boundingRect(polygon)
        return [(conf, mask, [x, y, x + w, y + h])] * n

    def test_prior_is_bottom_right_and_ellipse_like(self):
        prior = auto_wheel.prior_polygon(self.SIZE)
        cx, cy = prior.mean(axis=0)
        self.assertGreater(cx, 0.62 * self.SIZE[0])
        self.assertGreater(cy, 0.72 * self.SIZE[1])
        self.assertAlmostEqual(auto_wheel.polygon_iou(prior, prior, self.SIZE), 1.0)

    def test_low_confidence_detection_agreeing_with_prior_is_accepted(self):
        polygon, info = auto_wheel.resolve_wheel(
            self.candidates(auto_wheel.prior_polygon(self.SIZE)), self.SIZE)
        self.assertEqual(info['source'], 'detected')
        self.assertGreater(info['prior_iou'], 0.9)

    def test_detection_far_from_prior_is_rejected_unless_fallback_requested(self):
        far = auto_wheel.ellipse_polygon((700, 500), (200, 300), 0)
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            auto_wheel.resolve_wheel(self.candidates(far), self.SIZE)
        polygon, info = auto_wheel.resolve_wheel(self.candidates(far), self.SIZE, prior_fallback=True)
        self.assertEqual(info['source'], 'prior')
        np.testing.assert_array_equal(polygon, auto_wheel.prior_polygon(self.SIZE))

    def test_no_candidates_fails_strict_and_uses_labelled_prior_when_allowed(self):
        with self.assertRaisesRegex(ValueError, 'Need at least'):
            auto_wheel.resolve_wheel([], self.SIZE)
        _, info = auto_wheel.resolve_wheel([], self.SIZE, prior_fallback=True)
        self.assertEqual(info['source'], 'prior')

    def test_candidate_gate_rejects_crop_filling_and_far_away_masks(self):
        h, w = 200, 200
        frame = np.zeros((h, w, 3), np.uint8)

        def predict(mask_polygon):
            box = Mock()
            box.xyxy = [[0, 0, 5, 5]]
            box.conf.item.return_value = 0.5
            model = Mock()
            model.predict.return_value = [SimpleNamespace(
                masks=SimpleNamespace(xy=[np.array(mask_polygon, np.float32)]), boxes=[box])]
            return model

        x0, y0 = auto_wheel.roi_origin(w, h)
        cw, ch = w - x0, h - y0
        whole = [[0, 0], [cw, 0], [cw, ch], [0, ch]]             # fills the whole crop
        self.assertEqual(auto_wheel.wheel_candidates(predict(whole), frame), [])
        top_left = [[0, 0], [30, 0], [30, 30], [0, 30]]          # centroid outside the prior box
        self.assertEqual(auto_wheel.wheel_candidates(predict(top_left), frame), [])
        centre = [[cw * .45, ch * .35], [cw * .85, ch * .35], [cw * .85, ch * .75], [cw * .45, ch * .75]]
        self.assertEqual(len(auto_wheel.wheel_candidates(predict(centre), frame)), 1)

    def test_calibrator_locks_after_warmup_without_seeking(self):
        size = self.SIZE
        candidate = self.candidates(auto_wheel.prior_polygon(size), n=1)[0]
        calibrator = auto_wheel.WheelCalibrator(model=Mock(), warmup=4)
        frame = np.zeros((size[1], size[0], 3), np.uint8)
        with patch.object(auto_wheel, 'wheel_candidates', return_value=[candidate]):
            states = [calibrator.add_frame(frame) for _ in range(4)]
        self.assertEqual(states, ['collecting'] * 3 + ['locked'])
        self.assertEqual(calibrator.info['source'], 'detected')
        self.assertGreater(calibrator.margin, 0)
        self.assertEqual(calibrator.add_frame(frame), 'locked')   # no further inference

    def test_calibrator_reports_failure_when_fallback_is_disabled(self):
        calibrator = auto_wheel.WheelCalibrator(model=Mock(), warmup=3, prior_fallback=False)
        frame = np.zeros((100, 100, 3), np.uint8)
        with patch.object(auto_wheel, 'wheel_candidates', return_value=[]):
            for _ in range(3):
                calibrator.add_frame(frame)
        self.assertEqual(calibrator.status, 'failed')
        self.assertIn('Need at least', calibrator.error)


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


class PhoneRuleTests(unittest.TestCase):
    WHEEL = np.array([[1500, 1100], [1800, 1100], [1800, 1400], [1500, 1400]], np.int32)

    def kp(self, **pts):
        kp = np.full((17, 3), np.nan, np.float32)
        kp[rules.LSHO], kp[rules.RSHO] = [1000, 800, .9], [1300, 800, .9]      # shoulder width 300
        kp[rules.NOSE], kp[rules.LEAR] = [1150, 600, .9], [1080, 590, .9]
        for name, xy in pts.items():
            kp[getattr(rules, name)] = [*xy, .9]
        return kp

    def phones(self, *centers):
        p = np.full((3, 5), np.nan, np.float32)
        for i, (x, y) in enumerate(centers):
            p[i] = [x - 30, y - 50, x + 30, y + 50, .5]
        return p

    def test_hand_at_face_without_phone_is_not_phone_at_ear(self):
        f = rules.frame_flags(self.kp(LWR=(1090, 610)), self.phones(), self.WHEEL, 0)
        self.assertFalse(f['PHONE_AT_EAR'])
        self.assertTrue(f['hand_at_face'])

    def test_detected_phone_at_ear(self):
        f = rules.frame_flags(self.kp(LWR=(1090, 640)), self.phones((1085, 610)), self.WHEEL, 0)
        self.assertTrue(f['PHONE_AT_EAR'])
        self.assertFalse(f['PHONE_IN_HAND'])

    def test_phone_in_hand_low_even_if_other_hand_touches_face(self):
        f = rules.frame_flags(self.kp(LWR=(1090, 610), RWR=(1200, 1250)), self.phones((1210, 1260)), self.WHEEL, 0)
        self.assertTrue(f['PHONE_IN_HAND'])
        self.assertFalse(f['PHONE_AT_EAR'])


if __name__ == '__main__':
    unittest.main()
