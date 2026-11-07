"""Eyes-closed and drowsiness detector. Causal: uses only samples up to the current time.

Input per analyzed frame: blink score 0..1 (face model, near 1 = closed), the eye aspect ratio EAR measured on the
eyelid landmarks (about 0.25-0.4 open, <0.1 closed), the gaze-down score, or None when no face was found.

Why two signals: the blendshape blink score alone is noisy on this side-view cabin camera (an awake driver idles at
0.1-0.3 and reaches 0.4-0.6 when looking down or squinting). The landmark EAR is a different measurement of the same
thing, so a frame counts as closed only when the two agree (or when only one exists and it is strong).

Design:
  * per-driver references: the open-eye blink level (low percentile) and the open-eye EAR (high percentile) over the
    last REF_WINDOW_S seconds of NOT-closed frames, so cabin light, eye shape and view angle do not matter;
  * closure evidence 0..1 per signal, fused (mean when both exist). Looking down lowers the lids without closing the
    eyes: the blink is discounted by part of the gaze-down score and the EAR is raised by it;
  * hysteresis on the fused evidence, then the BLINK FILTER: 'closed' only after MIN_CLOSED_S of continuous closure
    (a blink is 0.1-0.4 s); one open/unknown gap of GAP_S inside a closure is tolerated; released after RELEASE_S;
  * HEAD DOWN: with the head pitched down (HEAD_DOWN_DEG above the driver's own forward pitch) the lids droop and
    the camera sees them from above, so looking at a phone or the lap is indistinguishable from closure (measured on
    real clips: blink 0.5-0.65 and EAR 0.1 both when reading a phone). That frame is treated as NOT OBSERVABLE
    (no observation, never 'closed'); a driver who slumps is caught by slump.py instead;
  * no face for more than UNKNOWN_S -> 'unknown' (bit 0, never held on without observations);
  * DROWSINESS: PERCLOS (share of the last PERCLOS_WINDOW_S the eyes were closed, the standard fatigue measure) and the
    number of long closures (microsleeps) in the last MICROSLEEP_WINDOW_S. `drowsy` is True when either is high.
The duration thresholds for the alert (drowsy / asleep) belong to the downstream engines; this reports how long the
eyes have been closed (closed_s), how long the closing took (onset_s) and the fatigue measures.
"""
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

REF_WINDOW_S = 60.0
BLINK_REF_PERCENTILE = 30
BLINK_REF_DEFAULT = 0.15
EAR_REF_PERCENTILE = 80
EAR_REF_DEFAULT = 0.28
REF_MIN_SAMPLES = 20
BLINK_OFFSET, BLINK_SPAN = 0.10, 0.35     # evidence 0 at ref+0.10, 1 at ref+0.45
EAR_OPEN_FRAC, EAR_SPAN = 0.70, 0.40      # evidence 0 at 70% of the open EAR, 1 at 30%
GAZE_DISCOUNT = 0.5                       # part of the gaze-down score taken off the blink score
GAZE_EAR_BOOST = 0.5                      # EAR is multiplied by 1 + this * gaze_down (lids lower when looking down)
ENTER, EXIT = 0.55, 0.35                  # hysteresis on the fused evidence
MIN_CLOSED_S = 0.5                        # blink filter
GAP_S = 0.25                              # tolerated open/unknown gap inside a closure
RELEASE_S = 0.3
UNKNOWN_S = 0.5
PERCLOS_WINDOW_S = 60.0
PERCLOS_MIN_S = 20.0                      # PERCLOS is only trusted after this much observed time
PERCLOS_DROWSY = 0.15
MICROSLEEP_WINDOW_S = 120.0
MICROSLEEP_DROWSY = 3                     # long closures in the window
HEAD_DOWN_DEG = 8.0                       # head pitch above the forward baseline at which the eyes are unobservable


@dataclass
class EyeObs:
    state: str                      # 'open' | 'closed' | 'unknown'
    raw_closed: Optional[bool]      # this frame alone, before the blink filter (None = no face)
    score: Optional[float]          # fused closure evidence 0..1
    ref: float                      # open-eye blink reference
    closed_s: float                 # seconds the eyes have been continuously closed (0 when not closed)
    onset_s: Optional[float]        # time the last closure took to go from open to closed (None if unknown)
    ear: Optional[float] = None     # eye aspect ratio this frame (None if not measured)
    ear_ref: float = EAR_REF_DEFAULT
    perclos: float = 0.0            # share of the last minute with closed eyes
    microsleeps: int = 0            # long closures in the last two minutes
    drowsy: bool = False


def _clip01(x):
    return float(min(max(x, 0.0), 1.0))


class EyeClosureDetector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.blink_hist = deque()           # (t, adjusted blink) of not-closed frames
        self.ear_hist = deque()             # (t, EAR) of not-closed frames
        self.raw_state = False
        self.run_start = None               # start of the current closed run
        self.last_closed_t = None
        self.open_since = None
        self.unknown_since = None
        self.state = 'unknown'
        self.rise_start = None              # when the evidence started rising (for onset_s)
        self.onset_s = None
        self.samples = deque()              # (t, dt, closed) for PERCLOS
        self.prev_t = None
        self.long_closures = deque()        # times when a closure became long

    @staticmethod
    def _percentile(hist, q, default):
        if len(hist) < REF_MIN_SAMPLES:
            return default
        return float(np.percentile([v for _, v in hist], q))

    @staticmethod
    def _trim(hist, t, window):
        while hist and t - hist[0][0] > window:
            hist.popleft()

    def update(self, t, blink, gaze_down=0.0, ear=None, pitch_delta=None) -> EyeObs:
        gaze = gaze_down or 0.0
        if pitch_delta is not None and pitch_delta >= HEAD_DOWN_DEG:
            blink = ear = None            # head down: the eyes cannot be judged
        if blink is None and ear is None:
            if self.unknown_since is None:
                self.unknown_since = t
            raw, score, blink_adj = None, None, None
        else:
            self.unknown_since = None
            blink_adj = None if blink is None else max(0.0, blink - GAZE_DISCOUNT * gaze)
        ref_b = self._percentile(self.blink_hist, BLINK_REF_PERCENTILE, BLINK_REF_DEFAULT)
        ref_e = self._percentile(self.ear_hist, EAR_REF_PERCENTILE, EAR_REF_DEFAULT)
        ear_adj = None if ear is None else ear * (1.0 + GAZE_EAR_BOOST * gaze)

        if not (blink is None and ear is None):
            parts = []
            if blink_adj is not None:
                parts.append(_clip01((blink_adj - ref_b - BLINK_OFFSET) / BLINK_SPAN))
            if ear_adj is not None and ref_e > 0:
                parts.append(_clip01((EAR_OPEN_FRAC - ear_adj / ref_e) / EAR_SPAN))
            score = float(np.mean(parts))
            self.raw_state = score >= (EXIT if self.raw_state else ENTER)
            raw = self.raw_state
            if not raw:                      # only open-eye samples feed the references
                if blink_adj is not None:
                    self.blink_hist.append((t, blink_adj))
                    self._trim(self.blink_hist, t, REF_WINDOW_S)
                if ear_adj is not None:
                    self.ear_hist.append((t, ear_adj))
                    self._trim(self.ear_hist, t, REF_WINDOW_S)
            if score > 0.2 and self.rise_start is None:
                self.rise_start = t
            elif score <= 0.2 and not raw:
                self.rise_start = None

        if raw:
            if self.run_start is None:
                self.run_start = t
                self.onset_s = None if self.rise_start is None else t - self.rise_start
            self.last_closed_t = t
            self.open_since = None
        elif raw is False:
            if self.open_since is None:
                self.open_since = t
            if self.run_start is not None and t - (self.last_closed_t or t) > GAP_S:
                self.run_start = None        # the closure ended before it became long
        else:                                # unknown frame
            if self.run_start is not None and t - (self.last_closed_t or t) > GAP_S:
                self.run_start = None

        closed_s = 0.0 if self.run_start is None else t - self.run_start
        was_closed = self.state == 'closed'
        if self.state == 'closed':
            if raw is False and self.open_since is not None and t - self.open_since >= RELEASE_S:
                self.state = 'open'
            elif raw is None and t - self.unknown_since > UNKNOWN_S:
                self.state = 'unknown'
        elif raw and closed_s >= MIN_CLOSED_S:
            self.state = 'closed'
        elif raw is False:
            self.state = 'open'
        elif raw is None and t - self.unknown_since > UNKNOWN_S:
            self.state = 'unknown'
        if self.state == 'closed' and not was_closed:
            self.long_closures.append(t)

        # fatigue measures
        if raw is not None and self.prev_t is not None:
            self.samples.append((t, min(t - self.prev_t, 0.5), bool(raw)))
        self.prev_t = t
        self._trim(self.samples, t, PERCLOS_WINDOW_S)
        while self.long_closures and t - self.long_closures[0] > MICROSLEEP_WINDOW_S:
            self.long_closures.popleft()
        observed = sum(dt for _, dt, _ in self.samples)
        perclos = sum(dt for _, dt, c in self.samples if c) / observed if observed >= PERCLOS_MIN_S else 0.0
        micro = len(self.long_closures)
        drowsy = perclos >= PERCLOS_DROWSY or micro >= MICROSLEEP_DROWSY

        shown = closed_s if self.state == 'closed' else 0.0
        return EyeObs(self.state, raw, score, ref_b, shown, self.onset_s, ear, ref_e, perclos, micro, drowsy)

    @staticmethod
    def raw_bit(obs):
        """1 closed, 0 open, None unknown (for the byte; the filtering is already inside update)."""
        return None if obs.state == 'unknown' else (1 if obs.state == 'closed' else 0)
