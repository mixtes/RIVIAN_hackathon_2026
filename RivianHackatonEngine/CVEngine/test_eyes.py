"""Tests for the head rule (eyes_off_road.py: turn, down, up), the flicker filter and the interface byte (cv_flags.py).
No model inference: keypoints are synthetic."""
import unittest

import numpy as np

from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv_flags
import eyes_off_road as eor
import head_face


def kp(yaw=eor.PRIOR_YAW, pitch=eor.PRIOR_PITCH, conf=0.9, eye_distance=40.0):
    """17x3 pose array whose head_measure is (yaw, pitch): eyes 40 px apart on one line, nose offset from it."""
    a = np.zeros((17, 3), np.float32)
    a[eor.LEYE] = [100.0 + eye_distance, 100, conf]
    a[eor.REYE] = [100.0, 100, conf]
    a[eor.NOSE] = [120.0 + yaw * eye_distance, 100 + pitch * eye_distance, conf]
    return a


def warmed(detector=None, n=60, **values):
    """A detector that has already seen n forward observations."""
    det = detector or eor.HeadPoseDetector()
    for i in range(n):
        det.update(i * 0.1, kp(**values))
    return det


class HeadMeasureTests(unittest.TestCase):
    def test_measurement_and_sign(self):
        yaw, pitch, reason = eor.head_measure(kp(yaw=0.25, pitch=0.7))
        self.assertAlmostEqual(yaw, 0.25, places=4)
        self.assertAlmostEqual(pitch, 0.7, places=4)
        self.assertIsNone(reason)
        self.assertAlmostEqual(eor.head_measure(kp(yaw=-0.6))[0], -0.6, places=4)
        self.assertAlmostEqual(eor.head_yaw(kp(yaw=0.25))[0], 0.25, places=4)

    def test_reasons_when_not_measurable(self):
        self.assertEqual(eor.head_measure(None), (None, None, 'no_driver'))
        self.assertEqual(eor.head_measure(kp(conf=0.2)), (None, None, 'low_confidence'))
        broken = kp()
        broken[eor.LEYE, 0] = broken[eor.REYE, 0]               # both eyes at the same x: no scale
        self.assertEqual(eor.head_measure(broken), (None, None, 'low_confidence'))


class ForwardStartTests(unittest.TestCase):
    """forward_start: the most frequent pose of the first seconds is the fixed baseline."""

    def seeded(self, **values):
        det = eor.HeadPoseDetector(forward_start=True)
        for i in range(int(eor.FORWARD_LOCK_S * 10) + 1):      # 10 Hz; silent for FORWARD_SEED_S, then provisional
            obs = det.update(i * 0.1, kp(**values))
            if i * 0.1 < eor.FORWARD_SEED_S:
                self.assertEqual(obs.state, 'unknown')
        return det, eor.FORWARD_LOCK_S + 0.1

    def test_detects_from_the_first_pose_even_far_from_the_prior(self):
        fwd = eor.PRIOR_YAW + 0.5                              # a forward pose far from the usual prior
        det, t = self.seeded(yaw=fwd)
        self.assertEqual(det.update(t, kp(yaw=fwd)).state, 'forward')
        self.assertEqual(det.update(t + 0.1, kp(yaw=fwd - 0.4)).state, 'turned')
        self.assertEqual(det.update(t + 0.2, kp(yaw=fwd + 0.4)).direction, 'right')

    def test_baseline_is_frozen_a_long_look_away_is_not_absorbed(self):
        det, t = self.seeded()
        for i in range(600):                                   # 60 s turned away
            obs = det.update(t + i * 0.1, kp(yaw=eor.PRIOR_YAW - 0.5))
        self.assertEqual(obs.state, 'turned')

    def test_a_glance_at_the_start_does_not_set_the_baseline(self):
        det = eor.HeadPoseDetector(forward_start=True)
        for i in range(15):                                    # 1.5 s looking at a passenger
            det.update(i * 0.1, kp(yaw=eor.PRIOR_YAW + 0.4))
        for i in range(15, 50):                                # then the road
            det.update(i * 0.1, kp())
        self.assertEqual(det.update(5.0, kp()).state, 'forward')
        self.assertEqual(det.update(5.1, kp(yaw=eor.PRIOR_YAW + 0.4)).state, 'turned')

    def test_default_mode_unchanged(self):
        self.assertFalse(eor.HeadPoseDetector().forward_start)


class LostHeadTests(unittest.TestCase):
    """A head that cannot be measured while the driver is still seen is 'away' (off road) after LOST_S."""

    def test_unmeasurable_head_becomes_away_after_a_delay(self):
        det = warmed()
        t = 6.0
        self.assertEqual(det.update(t, kp(conf=0.1)).state, 'unknown')
        self.assertEqual(det.update(t + eor.LOST_S - 0.1, kp(conf=0.1)).state, 'unknown')
        obs = det.update(t + eor.LOST_S, kp(conf=0.1))
        self.assertEqual((obs.state, obs.direction), ('away', 'away'))
        self.assertEqual(eor.HeadPoseDetector.raw_bit(obs), 1)
        self.assertEqual(det.update(t + 2, kp()).state, 'forward')          # head back: recovers at once

    def test_away_seconds_grow_while_lost(self):
        det = warmed()
        obs = [det.update(6.0 + i * 0.1, kp(conf=0.1)) for i in range(45)]
        self.assertAlmostEqual(obs[-1].away_s, 4.4, delta=0.01)        # lost since t = 6.0, last frame at 10.4

    def test_no_driver_is_never_away(self):
        det = warmed()
        for i in range(50):
            self.assertEqual(det.update(6.0 + i * 0.1, None).state, 'unknown')

    def test_no_keypoints_but_driver_seen_elsewhere_is_away(self):
        det = warmed()
        states = [det.update(6.0 + i * 0.1, None, driver_present=True).state for i in range(15)]
        self.assertEqual((states[0], states[-1]), ('unknown', 'away'))

    def test_not_away_before_any_baseline(self):
        det = eor.HeadPoseDetector()
        for i in range(50):
            self.assertEqual(det.update(i * 0.1, kp(conf=0.1)).state, 'unknown')


class HeadPoseDetectorTests(unittest.TestCase):
    """The pose-keypoint source (used when no face is found). Thresholds come from the constants."""

    def test_first_seconds_only_calibrate_then_detection_starts(self):
        det = eor.HeadPoseDetector()
        first = det.update(0.0, kp(yaw=eor.PRIOR_YAW - 0.5))           # a turn right at the start
        self.assertEqual((first.state, first.reason, first.baseline_source), ('unknown', 'calibrating', 'prior'))
        self.assertEqual(eor.HeadPoseDetector.raw_bit(first), None)       # so the bit stays 0
        for i in range(1, 60):
            det.update(i * 0.1, kp())
        self.assertEqual(det.update(eor.WARMUP_S + 1, kp(yaw=eor.PRIOR_YAW - 0.5)).state, 'turned')

    def test_baseline_becomes_measured_after_enough_observations(self):
        det = eor.HeadPoseDetector()
        for i in range(eor.MEASURED_AFTER - 1):
            self.assertEqual(det.update(i, kp()).baseline_source, 'prior')
        self.assertEqual(det.update(99, kp()).baseline_source, 'measured')

    def test_driver_with_a_different_forward_gets_their_own_baseline(self):
        det = warmed(yaw=0.30, n=150)                              # this driver's forward is +0.30
        self.assertAlmostEqual(det.baseline, 0.30, places=2)
        self.assertEqual(det.update(20, kp(yaw=0.30)).state, 'forward')
        self.assertEqual(det.update(21, kp(yaw=0.30 - 0.45)).state, 'turned')

    def test_a_short_look_far_from_the_prior_barely_moves_the_baseline(self):
        det = eor.HeadPoseDetector()
        for i in range(20):                                        # 2 s looking far away at the start
            det.update(i * 0.1, kp(yaw=-0.5))
        for i in range(40):
            det.update(2 + i * 0.1, kp(yaw=eor.PRIOR_YAW))
        self.assertAlmostEqual(det.baseline, eor.PRIOR_YAW, delta=0.05)

    def test_a_persistent_cluster_far_from_the_prior_still_wins(self):
        det = warmed(yaw=0.5, n=600)                               # a driver whose forward is far from the prior
        self.assertGreater(det.baseline, 0.4)

    def test_forward_prior_follows_the_head_position(self):
        def prior(x, y, ed):
            det = eor.HeadPoseDetector()
            k = kp(eye_distance=ed * 1000); k[0, :2] = (x * 1000, y * 1000); k[1, :2] = (x * 1000 + ed * 500, y * 1000)
            k[2, :2] = (x * 1000 - ed * 500, y * 1000)
            for i in range(6):
                det.update(i * 0.1, k, face(), (1000, 1000))
            return det.sources['face'].yaw_base.prior, det.sources['face'].pitch_base.prior
        self.assertNotEqual(prior(0.56, 0.45, 0.06), prior(0.64, 0.45, 0.06))
        yaw, pitch = prior(0.60, 0.45, 0.06)
        self.assertTrue(eor.FWD_YAW_LIMITS[0] <= yaw <= eor.FWD_YAW_LIMITS[1])
        self.assertTrue(eor.FWD_PITCH_LIMITS[0] <= pitch <= eor.FWD_PITCH_LIMITS[1])

    def test_without_a_frame_size_the_fixed_prior_is_used(self):
        det = eor.HeadPoseDetector()
        for i in range(6):
            det.update(i * 0.1, kp(), face())
        self.assertEqual(det.sources['face'].yaw_base.prior, eor.FACE_PRIOR_YAW)

    def test_baseline_is_the_most_frequent_pose_not_the_average(self):
        det = eor.HeadPoseDetector()
        for i in range(40):                                        # mostly forward ...
            det.update(i * 0.1, kp(yaw=eor.PRIOR_YAW))
        for i in range(25):                                        # ... with a long look elsewhere (would drag a median)
            det.update(4 + i * 0.1, kp(yaw=-0.2))
        self.assertAlmostEqual(det.baseline, eor.PRIOR_YAW, delta=0.03)

    def test_the_baseline_follows_the_driver_when_the_forward_cluster_changes(self):
        det = eor.HeadPoseDetector()
        for i in range(20):                                        # first seconds: looking somewhere else
            det.update(i * 0.1, kp(yaw=0.0))
        for i in range(120):                                       # then the road, for much longer
            det.update(2 + i * 0.1, kp(yaw=0.25))
        self.assertAlmostEqual(det.baseline, 0.25, delta=0.03)

    def test_turn_direction_and_hysteresis(self):
        det = warmed()
        base = det.baseline
        enter, leave = eor.POSE_YAW
        self.assertEqual(det.update(10, kp(yaw=base - (enter - 0.03))).state, 'forward')   # below the enter threshold
        left = det.update(10.1, kp(yaw=base - (enter + 0.15)))
        self.assertEqual((left.state, left.direction), ('turned', 'left'))
        self.assertLess(left.yaw_delta, 0)
        self.assertEqual(det.update(10.2, kp(yaw=base - (enter - 0.03))).state, 'turned')  # between exit and enter
        self.assertEqual(det.update(10.3, kp(yaw=base - (leave - 0.05))).state, 'forward')
        right = det.update(10.4, kp(yaw=base + enter + 0.15))
        self.assertEqual((right.state, right.direction), ('turned', 'right'))

    def test_head_down_and_hysteresis(self):
        det = warmed()
        pb = det.pitch_base.value
        enter, leave = eor.POSE_DOWN
        self.assertEqual(det.update(10, kp(pitch=pb + enter - 0.02)).state, 'forward')
        down = det.update(10.1, kp(pitch=pb + enter + 0.01))
        self.assertEqual((down.state, down.direction), ('down', 'down'))
        self.assertGreater(down.pitch_delta, 0)
        self.assertEqual(det.update(10.2, kp(pitch=pb + leave + 0.02)).state, 'down')      # above the exit threshold
        self.assertEqual(det.update(10.3, kp(pitch=pb + leave - 0.03)).state, 'forward')

    def test_head_up(self):
        det = warmed()
        pb = det.pitch_base.value
        up = det.update(10, kp(pitch=pb - 0.18))
        self.assertEqual((up.state, up.direction), ('up', 'up'))
        self.assertEqual(det.update(10.1, kp(pitch=pb - 0.05)).state, 'forward')

    def test_pitch_is_not_evaluated_while_the_head_is_turned(self):
        det = warmed()
        obs = det.update(10, kp(yaw=det.baseline - 0.6, pitch=det.pitch_base.value + 0.5))
        self.assertEqual(obs.state, 'turned')                       # yaw wins
        self.assertIsNone(obs.pitch_delta)

    def test_missing_face_is_unknown_never_off_road(self):
        det = warmed()
        for bad, reason in ((None, 'no_driver'), (kp(conf=0.1), 'low_confidence')):
            obs = det.update(10, bad)
            self.assertEqual((obs.state, obs.reason, obs.yaw, obs.source), ('unknown', reason, None, None))
            self.assertIsNone(det.raw_bit(obs))

    def test_raw_bit_mapping(self):
        det = warmed()
        base, pb = det.baseline, det.pitch_base.value
        self.assertEqual(det.raw_bit(det.update(10, kp(yaw=base, pitch=pb))), 0)
        self.assertEqual(det.raw_bit(det.update(10.1, kp(yaw=base - 0.6))), 1)
        self.assertEqual(det.raw_bit(det.update(10.2, kp(yaw=base, pitch=pb + 0.2))), 1)
        self.assertEqual(det.raw_bit(det.update(10.3, kp(yaw=base, pitch=pb - 0.2))), 1)

    def test_reset_returns_to_the_prior(self):
        det = warmed(yaw=0.30, n=150)
        det.reset()
        self.assertAlmostEqual(det.baseline, eor.PRIOR_YAW)
        self.assertEqual(det.update(0.0, kp()).baseline_source, 'prior')

    def test_old_name_still_works(self):
        self.assertIs(eor.HeadTurnDetector, eor.HeadPoseDetector)


def face(yaw=eor.FACE_PRIOR_YAW, pitch=eor.FACE_PRIOR_PITCH, blink=0.1, gaze_down=0.1):
    return head_face.FaceMeasure(yaw, pitch, 0.0, gaze_down, 0.05, blink)


class FaceSourceTests(unittest.TestCase):
    """The face source: angles in degrees, the same rule, a pose fallback."""

    def warmed_face(self, yaw=eor.FACE_PRIOR_YAW, pitch=eor.FACE_PRIOR_PITCH, n=60):
        det = eor.HeadPoseDetector()
        for i in range(n):
            det.update(i * 0.1, kp(), face(yaw, pitch))
        return det

    def test_face_is_preferred_and_reports_source_and_evidence(self):
        det = eor.HeadPoseDetector()
        obs = det.update(0.0, kp(), face(blink=0.8, gaze_down=0.3))
        self.assertEqual(obs.source, 'face')
        self.assertEqual((obs.blink, obs.gaze_down), (0.8, 0.3))
        self.assertEqual((obs.state, obs.reason), ('unknown', 'calibrating'))     # evidence is recorded meanwhile

    def test_a_22_degree_turn_triggers(self):
        det = self.warmed_face()
        obs = det.update(10, kp(), face(yaw=det.sources['face'].yaw_base.value - 22))
        self.assertEqual((obs.state, obs.direction), ('turned', 'left'))
        self.assertAlmostEqual(obs.yaw_delta, -22, places=3)

    def test_small_movement_stays_forward(self):
        det = self.warmed_face()
        base = det.sources['face'].yaw_base.value
        self.assertEqual(det.update(10, kp(), face(yaw=base + 15)).state, 'forward')

    def test_face_hysteresis_and_head_down(self):
        det = self.warmed_face()
        pb = det.sources['face'].pitch_base.value
        enter, leave = eor.FACE_DOWN
        self.assertEqual(det.update(10, kp(), face(pitch=pb + enter - 1)).state, 'forward')
        self.assertEqual(det.update(10.1, kp(), face(pitch=pb + enter + 1)).state, 'down')
        self.assertEqual(det.update(10.2, kp(), face(pitch=pb + leave + 1)).state, 'down')
        self.assertEqual(det.update(10.3, kp(), face(pitch=pb + leave - 1)).state, 'forward')

    def test_driver_specific_forward_in_degrees(self):
        det = self.warmed_face(yaw=5.0, n=150)                      # this driver looks forward at +5 deg
        self.assertAlmostEqual(det.sources['face'].yaw_base.value, 5.0, places=1)
        self.assertEqual(det.update(20, kp(), face(yaw=5.0)).state, 'forward')
        self.assertEqual(det.update(21, kp(), face(yaw=30.0)).state, 'turned')

    def test_falls_back_to_pose_when_no_face_is_found(self):
        det = eor.HeadPoseDetector()
        for i in range(60):
            det.update(i * 0.1, kp(), None)
        obs = det.update(7.0, kp(yaw=eor.PRIOR_YAW - 0.5), None)
        self.assertEqual((obs.source, obs.state), ('pose', 'turned'))
        self.assertIsNone(obs.blink)

    def test_unknown_only_when_neither_source_works(self):
        det = eor.HeadPoseDetector()
        self.assertEqual(det.update(0.0, None, None).state, 'unknown')
        self.assertEqual(det.update(0.1, kp(conf=0.1), None).reason, 'low_confidence')
        self.assertEqual(det.update(0.2, None, face()).source, 'face')   # a face alone is enough

    def test_pose_baseline_is_kept_fresh_while_the_face_decides(self):
        det = self.warmed_face()
        self.assertGreater(det.sources['pose'].yaw_base.n, 0)


class HeadFaceTests(unittest.TestCase):
    def rotation(self, yaw=0.0, pitch=0.0):
        cy, sy, cp, sp = np.cos(np.radians(yaw)), np.sin(np.radians(yaw)), np.cos(np.radians(pitch)), np.sin(np.radians(pitch))
        ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
        m = np.eye(4); m[:3, :3] = ry @ rx
        return m

    def test_euler_angles(self):
        self.assertEqual(head_face.euler_from_matrix(np.eye(4)), (0.0, 0.0, 0.0))
        yaw, pitch, _ = head_face.euler_from_matrix(self.rotation(yaw=30))
        self.assertAlmostEqual(yaw, 30, places=3)
        self.assertAlmostEqual(pitch, 0, places=3)
        self.assertAlmostEqual(head_face.euler_from_matrix(self.rotation(pitch=-12))[1], -12, places=3)

    def test_crop_box(self):
        self.assertIsNone(head_face.crop_box(None, (1536, 1920, 3)))
        self.assertIsNone(head_face.crop_box(kp(conf=0.1), (1536, 1920, 3)))
        a = kp(); a[eor.NOSE, :2] = [1000, 700]
        x0, y0, x1, y1 = head_face.crop_box(a, (1536, 1920, 3))
        self.assertEqual((x1 - x0, y1 - y0), (2 * int(3.4 * 40),) * 2)                    # half-size = 3.4 x eye distance
        small = kp(eye_distance=10.0); small[eor.NOSE, :2] = [1000, 700]                 # a far-away head: clamped to the minimum
        x0, y0, x1, y1 = head_face.crop_box(small, (1536, 1920, 3))
        self.assertEqual((x1 - x0, y1 - y0), (2 * head_face.CROP_MIN,) * 2)
        a[eor.NOSE, :2] = [5, 5]                                                         # near the corner: clipped to the frame
        self.assertEqual(head_face.crop_box(a, (1536, 1920, 3))[:2], (0, 0))

    def fake_result(self, matrix, blink=0.7):
        names = {'eyeBlinkLeft': blink, 'eyeBlinkRight': blink, 'eyeLookDownLeft': 0.4, 'eyeLookDownRight': 0.2,
                 'eyeLookUpLeft': 0.0, 'eyeLookUpRight': 0.1}
        return SimpleNamespace(face_landmarks=[object()], facial_transformation_matrixes=[matrix],
                               face_blendshapes=[[SimpleNamespace(category_name=k, score=v) for k, v in names.items()]])

    def test_measure(self):
        fh = head_face.FaceHead(landmarker=Mock())
        a = kp(); a[eor.NOSE, :2] = [800, 600]
        frame = np.zeros((1536, 1920, 3), np.uint8)
        with patch.object(fh, '_detect', return_value=self.fake_result(self.rotation(yaw=-22, pitch=9))):
            m = fh.measure(frame, a)
        self.assertAlmostEqual(m.yaw_deg, -22, places=3)
        self.assertAlmostEqual(m.pitch_deg, 9, places=3)
        self.assertAlmostEqual(m.blink, 0.7)
        self.assertAlmostEqual(m.gaze_down, 0.3)

    def test_no_face_or_no_head_location_gives_none(self):
        fh = head_face.FaceHead(landmarker=Mock())
        frame = np.zeros((1536, 1920, 3), np.uint8)
        empty = SimpleNamespace(face_landmarks=[], facial_transformation_matrixes=[], face_blendshapes=[])
        a = kp(); a[eor.NOSE, :2] = [800, 600]
        with patch.object(fh, '_detect', return_value=empty):
            self.assertIsNone(fh.measure(frame, a))
        with patch.object(fh, '_detect') as detect:
            self.assertIsNone(fh.measure(frame, None))
        detect.assert_not_called()


class FlickerFilterTests(unittest.TestCase):
    def run_filter(self, samples, **kw):
        f = cv_flags.FlickerFilter(**kw)
        return [f.update(t, raw) for t, raw in samples]

    def test_switches_after_the_delay_in_both_directions(self):
        samples = [(i * 0.1, 1 if 1.0 <= i * 0.1 < 3.0 else 0) for i in range(50)]
        out = self.run_filter(samples)
        rise = next(t for (t, _), o in zip(samples, out) if o == 1)
        fall = next(t for (t, _), o in zip(samples, out) if t > rise and o == 0)
        self.assertAlmostEqual(rise, 1.3, places=1)       # raw rose at 1.0, +0.3 s
        self.assertAlmostEqual(fall, 3.3, places=1)       # raw fell at 3.0, +0.3 s: symmetric, so no bias in duration

    def test_short_blips_are_suppressed_in_both_directions(self):
        up = self.run_filter([(i * 0.1, 1 if i in (5, 6) else 0) for i in range(15)])
        self.assertEqual(sum(up), 0)
        down = self.run_filter([(i * 0.1, 0 if i in (20, 21) else 1) for i in range(40)])
        self.assertEqual(down[-1], 1)
        self.assertNotIn(0, down[5:])

    def test_unknown_gap_keeps_state_briefly_then_drops_to_zero(self):
        f = cv_flags.FlickerFilter()
        for i in range(10):
            f.update(i * 0.1, 1)                          # on since 0.3 s
        self.assertEqual(f.out, 1)
        self.assertEqual(f.update(1.0, None), 1)           # unobservable, within unknown_s
        self.assertEqual(f.update(1.4, None), 1)
        self.assertEqual(f.update(1.6, None), 0)           # more than 0.5 s unobservable: never held on
        self.assertEqual(f.update(1.7, 1), 0)              # needs the on-delay again

    def test_reset(self):
        f = cv_flags.FlickerFilter()
        for i in range(10):
            f.update(i * 0.1, 1)
        f.reset()
        self.assertEqual(f.out, 0)

    def test_replay_matches_the_cut2_shape(self):
        det, filt, bits = eor.HeadPoseDetector(), cv_flags.FlickerFilter(), []
        for i in range(0, 400, 2):                          # 30 fps source, every 2nd frame
            t = i / 30
            yaw = 0.12 if t < 10.9 else (-0.55 if t < 12.6 else 0.12)
            bits.append((t, filt.update(t, det.raw_bit(det.update(t, kp(yaw=yaw))))))
        on = [t for t, b in bits if b == 1]
        self.assertAlmostEqual(on[0], 10.9 + 0.3, delta=0.15)
        self.assertAlmostEqual(on[-1], 12.6 + 0.3, delta=0.15)
        self.assertEqual(bits[0][1], 0)                     # no blind period, no false start


class FlagByteTests(unittest.TestCase):
    def test_layout_matches_the_interface_document(self):
        self.assertEqual((cv_flags.ASLEEP, cv_flags.OFF_ROAD, cv_flags.HANDS_OFF, cv_flags.PHONE, cv_flags.YAWN),
                         (0x80, 0x40, 0x20, 0x10, 0x08))
        self.assertEqual(cv_flags.RESERVED, 0x07)

    def test_stream_record_format(self):
        self.assertEqual(cv_flags.format_record(12.3456, 0x60), '12.346,96')
        self.assertEqual(cv_flags.format_record(0, 0x1FF), '0.000,255')       # always a byte
        self.assertEqual(cv_flags.format_record(12.3456, 0x60, 3), '12.346,96,3')   # with the occupant count
        self.assertEqual(cv_flags.format_record(1.0, 0, -2), '1.000,0,0')           # never negative

    def test_pack(self):
        self.assertEqual(cv_flags.pack_flags(), 0x00)
        self.assertEqual(cv_flags.pack_flags(hands_off=True), 0x20)
        self.assertEqual(cv_flags.pack_flags(hands_off=True, off_road=True), 0x60)
        self.assertEqual(cv_flags.pack_flags(asleep=True, off_road=True, hands_off=True, phone=True), 0xF0)
        for combo in range(16):
            v = cv_flags.pack_flags(bool(combo & 8), bool(combo & 4), bool(combo & 2), bool(combo & 1))
            self.assertEqual(v & cv_flags.RESERVED, 0)       # the reserved nibble is always 0
            self.assertTrue(0 <= v <= 255)

    def test_priority_is_numeric_order(self):
        self.assertGreater(cv_flags.ASLEEP, cv_flags.OFF_ROAD)
        self.assertGreater(cv_flags.OFF_ROAD, cv_flags.HANDS_OFF)
        self.assertGreater(cv_flags.HANDS_OFF, cv_flags.PHONE)


if __name__ == '__main__':
    unittest.main()
