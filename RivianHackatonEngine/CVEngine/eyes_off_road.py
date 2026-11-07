"""EYES_OFF_ROAD: head turned left/right, or head down/up. Face angles when a face is found, pose keypoints otherwise.

Two measurement sources, the same rule on both:
  face  (head_face.py)  yaw / pitch in DEGREES from MediaPipe FaceLandmarker. Accurate. Found in 81 to 98% of frames.
  pose  (nose and eyes of the pose model, below)  unit-less proxies, used only for frames where no face is found:
          yaw   = (nose_x - eye_midpoint_x) / eye_distance     about 0.35 per 37 degrees
          pitch = (nose_y - eye_line_y)    / eye_distance      weak signal; blind to some looks down
Pitch is only evaluated while the head is not turned (a turned head distorts it). Eye closure (blink) and eye gaze from
the face model are carried along as evidence for the future sleep detection; they do not feed this rule.

Baselines: what 'forward' looks like differs per driver and per seat position (the camera sees the driver from the
side; forward yaw measured -35 to +51 deg over 50 clips, mostly near +8 or +33). Calibration:
  1. PRIOR: the forward angle is predicted from where the head sits in the frame (nose x, y, eye distance; linear fit on
     the 50 clips, leave-one-clip-out error 7.7 deg yaw / 5.4 deg pitch). Without a frame size, a fixed prior is used.
  2. WARM-UP: the first WARMUP_S seconds only collect observations (state 'unknown', reason 'calibrating', bit stays 0).
  3. LEARNING: the baseline is the driver's most frequent pose over the last ~2 minutes (a mode, so glances do not drag
     it), every observation weighted by a Gaussian around the prior (a short look at the lap carries little weight, a
     large persistent cluster still wins). It keeps following the driver.
Replayed on 42 clips against the full-video mode: baseline error 7.0 / 5.1 / 2.9 deg yaw at 8 / 15 / 29 s, against
10.4 / 8.5 / 5.7 deg for the earlier fixed-prior median. Known weakness: a driver who is not looking at the road for
most of the first ~15 s still gets a wrong baseline until the road pose is the most frequent. The coefficients are
from one cabin camera: refit (see FWD_*_COEF) for another car or camera.

Sensitivity: ANY head movement away from forward past the threshold sets the state. Allowed movements (a mirror check, a
look into the turn) are NOT filtered here by a high threshold: they are short, and the AlertEngine / GameEngine own the
duration thresholds (see CV_ENGINE_INTERFACE.md). The only smoothing here is the flicker filter in cv_flags.py.

Rule: turned when |yaw - baseline| >= ENTER (leaves below EXIT; hysteresis); down when pitch - baseline >= DOWN_ENTER;
up when baseline - pitch >= UP_ENTER. 'Up' has hardly any validation data. 'left'/'right' are the sides of the IMAGE.
No face and no usable pose keypoints -> 'unknown', never off-road. A head turned so far that nose and eyes vanish also
reads 'unknown' (known limitation).
"""
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

NOSE, LEYE, REYE = 0, 1, 2
HEAD_KP_CONF = 0.5          # pose fallback: nose and both eyes must be at least this confident

# ---- pose-proxy source (fallback) ------------------------------------------------------------------------------
PRIOR_YAW, PRIOR_PITCH = 0.09, 0.56
YAW_RANGE, PITCH_RANGE = 0.30, 0.10
POSE_YAW = (0.20, 0.14)         # (enter, exit): about 21 degrees
POSE_DOWN = (0.12, 0.08)
POSE_UP = (0.15, 0.10)

# ---- face source, degrees ---------------------------------------------------------------------------------------
FACE_PRIOR_YAW, FACE_PRIOR_PITCH = 20.0, 16.0
FACE_YAW_RANGE, FACE_PITCH_RANGE = 20.0, 10.0
FACE_YAW = (20.0, 14.0)
FACE_DOWN = (12.0, 8.0)
FACE_UP = (15.0, 10.0)

# Forward head angle predicted from where the driver's head sits in the frame (x, y of the nose / frame width, height;
# eye distance / frame width). Fitted on 42 of 50 clips (the rest had no clear forward pose); leave-one-clip-out error
# 7.7 deg yaw (p90 15.8) and 5.4 deg pitch (p90 10.4). The camera sees different seat positions at different angles.
FWD_YAW_COEF = (173.971, -109.397, -1017.721, 8.908)     # x, y, eye distance, 1
FWD_PITCH_COEF = (-28.825, 158.203, 616.476, -60.042)
FWD_YAW_LIMITS, FWD_PITCH_LIMITS = (-5.0, 50.0), (-5.0, 35.0)
POS_YAW_RANGE, POS_PITCH_RANGE = 20.0, 10.0      # width (sigma) of the plausibility weight around the predicted forward
POSITION_SAMPLES = 150                            # recent head positions whose median feeds the prediction
PRIOR_SECONDS = 4.0         # the prior counts as this many seconds of observations (frame-rate independent)
PRIOR_WEIGHT = 30           # the same in samples, used until the observation rate is known
WARMUP_S = 4.0              # calibration time: the baseline is learned, nothing is reported off-road (bit stays 0)
WARMUP_MIN_SAMPLES = 15     # ... and at least this many observations must have been collected
BIN_FACE, BIN_POSE = 2.0, 0.02   # histogram bin of the baseline mode, degrees / proxy units
MEASURED_AFTER = 30         # observations after which a baseline is reported as 'measured'
BASELINE_SAMPLES = 900      # newest observations kept for the running median


def head_measure(kp) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Pose proxies -> (yaw, pitch, reason). None, with a reason, when they cannot be measured."""
    if kp is None:
        return None, None, 'no_driver'
    if min(kp[NOSE, 2], kp[LEYE, 2], kp[REYE, 2]) < HEAD_KP_CONF:
        return None, None, 'low_confidence'
    eye_distance = abs(float(kp[LEYE, 0] - kp[REYE, 0]))
    if eye_distance < 1e-3:
        return None, None, 'low_confidence'
    yaw = (float(kp[NOSE, 0]) - (float(kp[LEYE, 0]) + float(kp[REYE, 0])) / 2) / eye_distance
    pitch = (float(kp[NOSE, 1]) - (float(kp[LEYE, 1]) + float(kp[REYE, 1])) / 2) / eye_distance
    return yaw, pitch, None


def head_yaw(kp):
    yaw, _, reason = head_measure(kp)
    return yaw, reason


class RunningBaseline:
    """The driver's MOST FREQUENT head pose over the recent window (peak of a smoothed histogram, refined by the median
    of the samples around it), mixed with PRIOR_WEIGHT pseudo-samples at the prior. Every observation is weighted by a
    Gaussian of width `limit` around the prior: a short look elsewhere carries little weight, a large persistent
    cluster still wins, so the baseline can follow a driver whose forward is far from the prediction.
    A driver spends most of the drive looking at the road, so the mode is that view even when the first seconds were
    spent looking elsewhere (a median is dragged by every glance). Causal: only observations up to now are used."""

    def __init__(self, prior, limit, bin_width=1.0):
        self.prior, self.limit, self.bin = prior, limit, bin_width
        self.prior_weight = PRIOR_WEIGHT
        self.samples = deque(maxlen=BASELINE_SAMPLES)

    def add(self, value):
        self.samples.append(value)

    @property
    def n(self):
        return len(self.samples)

    @property
    def value(self):
        if not self.samples:
            return self.prior
        values = np.concatenate([np.full(int(round(self.prior_weight)), self.prior), np.fromiter(self.samples, float)])
        weights = np.exp(-0.5 * ((values - self.prior) / self.limit) ** 2)      # plausibility under the prior
        lo, hi = values.min() - self.bin, values.max() + 2 * self.bin
        edges = np.arange(lo, hi + self.bin, self.bin)
        hist, _ = np.histogram(values, bins=edges, weights=weights)
        peak = int(np.argmax(np.convolve(hist, [1, 2, 3, 2, 1], mode='same')))
        centre = edges[peak] + self.bin / 2
        near = np.abs(values - centre) <= 2 * self.bin
        order = np.argsort(values[near])
        cum = np.cumsum(weights[near][order])
        return float(values[near][order][np.searchsorted(cum, cum[-1] / 2)])


@dataclass
class HeadObservation:
    state: str                          # 'forward' | 'turned' | 'down' | 'up' | 'unknown'
    yaw: Optional[float]                # degrees for source 'face', proxy units for source 'pose'
    pitch: Optional[float]
    yaw_delta: Optional[float]          # yaw - yaw baseline, same units
    pitch_delta: Optional[float]        # None while the head is turned (not evaluated)
    direction: Optional[str]            # 'left' | 'right' (image sides) | 'down' | 'up' while off road, else None
    reason: Optional[str]               # why unknown: 'no_driver' | 'low_confidence'
    baseline_source: str                # 'prior' until MEASURED_AFTER observations, then 'measured'
    source: Optional[str] = None        # 'face' | 'pose' | None when unknown
    blink: Optional[float] = None       # eye closure 0..1 (face only), evidence for sleep detection
    gaze_down: Optional[float] = None   # 0..1 (face only)

    @property
    def delta(self):
        return self.yaw_delta


class _Source:
    """Baselines and thresholds of one measurement source."""

    def __init__(self, prior_yaw, yaw_range, prior_pitch, pitch_range, yaw, down, up, bin_width):
        self.yaw_base = RunningBaseline(prior_yaw, yaw_range, bin_width)
        self.pitch_base = RunningBaseline(prior_pitch, pitch_range, bin_width)
        self.yaw_t, self.down_t, self.up_t = yaw, down, up

    def feed(self, yaw, pitch, turned_now):
        self.yaw_base.add(yaw)
        if not turned_now:
            self.pitch_base.add(pitch)


class HeadPoseDetector:
    """Causal: update(t, kp, face) uses only observations up to t. reset() between unrelated clips and after a seek."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.sources = {
            'face': _Source(FACE_PRIOR_YAW, FACE_YAW_RANGE, FACE_PRIOR_PITCH, FACE_PITCH_RANGE,
                            FACE_YAW, FACE_DOWN, FACE_UP, BIN_FACE),
            'pose': _Source(PRIOR_YAW, YAW_RANGE, PRIOR_PITCH, PITCH_RANGE, POSE_YAW, POSE_DOWN, POSE_UP, BIN_POSE)}
        self._t0 = None
        self._rate_t0 = self._rate_n = None
        self._pos = deque(maxlen=POSITION_SAMPLES)
        self._turned = self._down = self._up = False

    @property
    def yaw_base(self):                 # kept for callers and tests of the pose source
        return self.sources['pose'].yaw_base

    @property
    def pitch_base(self):
        return self.sources['pose'].pitch_base

    @property
    def baseline(self):
        return self.sources['pose'].yaw_base.value

    def _decide(self, name, yaw, pitch, evidence=None):
        src = self.sources[name]
        base_yaw, base_pitch = src.yaw_base.value, src.pitch_base.value
        yaw_delta = yaw - base_yaw
        turned_now = abs(yaw_delta) >= src.yaw_t[1 if self._turned else 0]
        src.feed(yaw, pitch, turned_now)
        source = 'measured' if src.yaw_base.n >= MEASURED_AFTER else 'prior'
        blink, gaze_down = evidence if evidence else (None, None)
        self._turned = turned_now
        if turned_now:
            self._down = self._up = False
            return HeadObservation('turned', yaw, pitch, yaw_delta, None, 'left' if yaw_delta < 0 else 'right',
                                   None, source, name, blink, gaze_down)
        pitch_delta = pitch - base_pitch
        self._down = pitch_delta >= src.down_t[1 if self._down else 0]
        self._up = (not self._down) and -pitch_delta >= src.up_t[1 if self._up else 0]
        state, direction = ('down', 'down') if self._down else (('up', 'up') if self._up else ('forward', None))
        return HeadObservation(state, yaw, pitch, yaw_delta, pitch_delta, direction, None, source, name,
                               blink, gaze_down)

    def _track_rate(self, t, kp, face):
        """Express the prior's weight in seconds: pseudo-samples = PRIOR_SECONDS x observations per second."""
        if face is None and kp is None:
            return
        if self._rate_t0 is None:
            self._rate_t0, self._rate_n = t, 0
        self._rate_n += 1
        elapsed = t - self._rate_t0
        rate = self._rate_n / elapsed if elapsed >= 1.0 else PRIOR_WEIGHT / PRIOR_SECONDS
        for source in self.sources.values():
            source.yaw_base.prior_weight = source.pitch_base.prior_weight = PRIOR_SECONDS * min(max(rate, 1.0), 30.0)

    def _track_position(self, kp, frame_size):
        """Remember where the head is (nose x, y, eye distance, normalised) and aim the face baselines at the forward
        angle predicted for that position."""
        if kp is None or frame_size is None or min(kp[NOSE, 2], kp[LEYE, 2], kp[REYE, 2]) < HEAD_KP_CONF:
            return
        w, h = frame_size
        self._pos.append((kp[NOSE, 0] / w, kp[NOSE, 1] / h, abs(kp[LEYE, 0] - kp[REYE, 0]) / w))
        if len(self._pos) >= 5:
            x, y, ed = np.median(np.array(self._pos), axis=0)
            feats = np.array([x, y, ed, 1.0])
            face = self.sources['face']
            face.yaw_base.prior = float(np.clip(feats @ FWD_YAW_COEF, *FWD_YAW_LIMITS))
            face.pitch_base.prior = float(np.clip(feats @ FWD_PITCH_COEF, *FWD_PITCH_LIMITS))
            face.yaw_base.limit, face.pitch_base.limit = POS_YAW_RANGE, POS_PITCH_RANGE

    def update(self, t, kp, face=None, frame_size=None):
        """kp: pose keypoints of the driver or None. face: head_face.FaceMeasure or None. frame_size: (width, height)
        of the frame; with it the forward angle is predicted from the head position (without it, a fixed prior).
        The first WARMUP_S seconds only calibrate: the state is 'unknown' (reason 'calibrating'), so the bit stays 0."""
        self._track_position(kp, frame_size)
        self._track_rate(t, kp, face)
        obs = self._update(t, kp, face)
        if obs.state != 'unknown' and self._t0 is None:
            self._t0 = t
        if obs.state == 'unknown' and obs.reason != 'calibrating' or self._t0 is None:
            return obs
        n = self.sources['face'].yaw_base.n + self.sources['pose'].yaw_base.n
        if t - self._t0 >= WARMUP_S and n >= WARMUP_MIN_SAMPLES:
            return obs
        self._turned = self._down = self._up = False
        return HeadObservation('unknown', obs.yaw, obs.pitch, None, None, None, 'calibrating', 'prior', obs.source,
                               obs.blink, obs.gaze_down)

    def _update(self, t, kp, face=None):
        pose_yaw, pose_pitch, reason = head_measure(kp)
        if pose_yaw is not None:           # keep the pose baselines fresh so the fallback is ready at any moment
            pose = self.sources['pose']
            pose.feed(pose_yaw, pose_pitch, abs(pose_yaw - pose.yaw_base.value) >= pose.yaw_t[0])
        if face is not None:
            return self._decide('face', face.yaw_deg, face.pitch_deg, (face.blink, face.gaze_down))
        if pose_yaw is None:
            self._turned = self._down = self._up = False
            return HeadObservation('unknown', None, None, None, None, None, reason,
                                   'measured' if self.sources['pose'].yaw_base.n >= MEASURED_AFTER else 'prior')
        # the pose baselines were already fed above, so decide without feeding them twice
        src = self.sources['pose']
        yaw_delta = pose_yaw - src.yaw_base.value
        self._turned = abs(yaw_delta) >= src.yaw_t[1 if self._turned else 0]
        source = 'measured' if src.yaw_base.n >= MEASURED_AFTER else 'prior'
        if self._turned:
            self._down = self._up = False
            return HeadObservation('turned', pose_yaw, pose_pitch, yaw_delta, None,
                                   'left' if yaw_delta < 0 else 'right', None, source, 'pose')
        pitch_delta = pose_pitch - src.pitch_base.value
        self._down = pitch_delta >= src.down_t[1 if self._down else 0]
        self._up = (not self._down) and -pitch_delta >= src.up_t[1 if self._up else 0]
        state, direction = ('down', 'down') if self._down else (('up', 'up') if self._up else ('forward', None))
        return HeadObservation(state, pose_yaw, pose_pitch, yaw_delta, pitch_delta, direction, None, source, 'pose')

    @staticmethod
    def raw_bit(observation):
        """1 = off road (turned, down or up), 0 = forward, None = could not observe (what FlickerFilter expects)."""
        if observation.state == 'unknown':
            return None
        return 0 if observation.state == 'forward' else 1


HeadTurnDetector = HeadPoseDetector       # the earlier name
