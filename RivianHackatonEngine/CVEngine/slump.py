"""Slump / loss-of-posture detector (health event: fainted, unresponsive, fell asleep and dropped forward). Causal.

Why it exists: when a driver collapses, the face is no longer visible (head down, turned into the seat or onto the
chest), so every face-based cue (eye closure, head angle) goes 'unknown'. The BODY stays visible, and it shows the
clearest signature there is: the head, normally well above the shoulder line, falls to or below it.

Measure, from the 2D pose keypoints (COCO order: 0 nose, 1-2 eyes, 3-4 ears, 5-6 shoulders):
  ratio = (shoulder_y - head_y) / shoulder_width      (image y grows downward)
  upright driver ~ +0.55 (head about half a shoulder-width above the shoulders); slumped: <= 0.2, negative when the
  head is below the shoulders. On the real collapse clip it goes +0.57 -> -0.1 in 0.5 s and stays at -0.3 to -0.6.

  * per-driver baseline: 75th percentile of the upright ratios of the last BASE_WINDOW_S seconds (prior 0.55, clamped),
    so seat position and camera angle do not matter;
  * slumped (raw) when ratio < min(ENTER_FRAC * baseline, ENTER_MAX): the head fell well below this driver's normal
    AND is at the shoulder line (ratio < 0.10), so leaning forward or reaching across with the head still up is not
    a slump; leaves above min(EXIT_FRAC * baseline, EXIT_MAX) (hysteresis);
  * a slumped driver is often lost by the person detector for a few frames: for up to LOST_HOLD_S after the last
    slumped observation a missing driver counts as still slumped; longer than that -> 'unknown' (bit 0, never held
    on without observations);
  * confirmed 'slumped' after MIN_SLUMP_S of continuous raw slump (filters a reach to the footwell / glovebox
    lasting a moment); released after RELEASE_S upright.
The engines own the alert durations; this reports slumped_s (how long).
"""
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

HEAD_KP = (0, 1, 2, 3, 4)
L_SH, R_SH = 5, 6
KP_CONF = 0.3
BASE_WINDOW_S = 120.0
BASE_PERCENTILE = 75
BASE_DEFAULT, BASE_MIN, BASE_MAX = 0.55, 0.35, 0.9
BASE_MIN_SAMPLES = 15
ENTER_FRAC, EXIT_FRAC = 0.45, 0.65
ENTER_MAX, EXIT_MAX = 0.10, 0.22            # absolute guard: the head must be at the shoulder line (a forward lean is not a slump)
MIN_SLUMP_S = 0.6
RELEASE_S = 1.0
LOST_HOLD_S = 2.5
UNKNOWN_S = 0.5


@dataclass
class SlumpObs:
    state: str                      # 'upright' | 'slumped' | 'unknown'
    raw_slumped: Optional[bool]     # this frame alone (None = could not observe)
    ratio: Optional[float]          # (shoulder_y - head_y) / shoulder_width, None when not measurable
    baseline: float                 # this driver's upright ratio
    slumped_s: float                # seconds continuously slumped (0 when not confirmed)
    held: bool = False              # raw value carried over from a lost-driver gap


def head_shoulder_ratio(kp):
    """(shoulder_y - head_y) / shoulder_width from pose keypoints, or None when not enough is visible."""
    if kp is None:
        return None
    kp = np.asarray(kp, float)
    sh = [i for i in (L_SH, R_SH) if kp[i, 2] >= KP_CONF and not np.isnan(kp[i, 0])]
    if len(sh) < 2:
        return None
    head = [i for i in HEAD_KP if kp[i, 2] >= KP_CONF and not np.isnan(kp[i, 0])]
    if not head:
        return None
    width = abs(kp[L_SH, 0] - kp[R_SH, 0])
    if width < 20:                    # shoulders edge-on or noise: the ratio is meaningless
        return None
    return float((kp[sh, 1].mean() - kp[head, 1].mean()) / width)


class SlumpDetector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.hist = deque()                 # (t, ratio) of upright frames
        self.raw_state = False
        self.run_start = None
        self.upright_since = None
        self.last_obs_t = None              # last time the driver was measured
        self.last_slumped_obs_t = None      # last measured frame that was slumped
        self.unknown_since = None
        self.state = 'unknown'

    def _baseline(self):
        if len(self.hist) < BASE_MIN_SAMPLES:
            return BASE_DEFAULT
        b = float(np.percentile([r for _, r in self.hist], BASE_PERCENTILE))
        return min(max(b, BASE_MIN), BASE_MAX)

    def update(self, t, kp) -> SlumpObs:
        ratio = head_shoulder_ratio(kp)
        base = self._baseline()
        held = False
        if ratio is not None:
            self.unknown_since = None
            self.last_obs_t = t
            limit = min(EXIT_FRAC * base, EXIT_MAX) if self.raw_state else min(ENTER_FRAC * base, ENTER_MAX)
            self.raw_state = ratio < limit
            raw = self.raw_state
            if raw:
                self.last_slumped_obs_t = t
            else:
                self.hist.append((t, ratio))
                while self.hist and t - self.hist[0][0] > BASE_WINDOW_S:
                    self.hist.popleft()
        else:
            if self.unknown_since is None:
                self.unknown_since = t
            if self.raw_state and self.last_slumped_obs_t is not None and t - self.last_slumped_obs_t <= LOST_HOLD_S:
                raw, held = True, True        # lost right after a slump: still slumped
            else:
                raw = None

        if raw:
            if self.run_start is None:
                self.run_start = t
            self.upright_since = None
        elif raw is False:
            self.run_start = None
            if self.upright_since is None:
                self.upright_since = t
        else:
            self.run_start = None if self.state != 'slumped' else self.run_start

        slumped_s = 0.0 if self.run_start is None else t - self.run_start
        if self.state == 'slumped':
            if raw is False and self.upright_since is not None and t - self.upright_since >= RELEASE_S:
                self.state = 'upright'
            elif raw is None and self.unknown_since is not None and t - self.unknown_since > UNKNOWN_S:
                self.state = 'unknown'
        elif raw and slumped_s >= MIN_SLUMP_S:
            self.state = 'slumped'
        elif raw is False:
            self.state = 'upright'
        elif raw is None and self.unknown_since is not None and t - self.unknown_since > UNKNOWN_S:
            self.state = 'unknown'
        return SlumpObs(self.state, raw, ratio, base, slumped_s if self.state == 'slumped' else 0.0, held)

    @staticmethod
    def raw_bit(obs):
        """1 slumped, 0 upright, None unknown."""
        return None if obs.state == 'unknown' else (1 if obs.state == 'slumped' else 0)
