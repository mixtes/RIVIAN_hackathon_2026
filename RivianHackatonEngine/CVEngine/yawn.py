"""Yawn detection from the face (mouth opening) and the hand (covering the lips). Causal: only past frames.

A yawn starts with the mouth opening wide. People then usually cover it with a hand, and the face model can no
longer see the open mouth (jawOpen drops to ~0 behind the hand). A hand at the mouth on its own is NOT a yawn:
in the dataset it is just as often a chin rest, smoking, coughing, rubbing the face or eating. What separates a
yawn is the ORDER (checked on all 116 clips: fires on the 2 visible yawns, 010329 and 084539, and nowhere else):

  covered yawn:   mouth wide open (jawOpen >= JAW_OPEN in >= OPEN_FRAMES analyzed frames within OPEN_WINDOW_S)
                  with the hand NOT on the lips, then within COVER_WITHIN_S a hand covers the lips.
  uncovered yawn: mouth wide open, hand away, for at least UNCOVERED_S (not seen in the dataset; kept strict).
  Eating is the reverse order (hand with food at the lips first, then the mouth opens, then the hand leaves);
  talking opens the mouth for single frames only.

The yawn state stays on while the hand covers the lips or the mouth is still wide open, and ends after END_S
without either (or after MAX_S). The bit is a state for the engines (cv_flags.YAWN, 0x08); it needs --head face.
"""
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

JAW_OPEN = 0.3          # jawOpen blendshape: wide-open mouth (yawn frames 0.3-0.42; talking mostly single frames)
OPEN_FRAMES = 2         # wide-open frames needed (1 frame also fired on snack eating)
OPEN_WINDOW_S = 1.0     # ...within this time
COVER_WITHIN_S = 1.5    # a hand must cover the lips this soon after the mouth opened
LIPS_DIST = 0.15        # hand point within this many face widths of the lips centre = covering the lips
UNCOVERED_S = 1.5       # mouth wide open without a hand for this long is also a yawn
END_S = 0.6             # yawn ends after this long with neither a covering hand nor an open mouth
MAX_S = 8.0             # no yawn lasts longer than this
LIPS_MAX_AGE_S = 3.0    # the last seen lips position is reused this long while the hand hides the face


@dataclass
class YawnState:
    state: str                       # 'yawn' | 'none'
    reason: str = ''
    jaw_open: Optional[float] = None
    covered: Optional[bool] = None   # hand on the lips (None = not checked this frame)
    started_s: Optional[float] = None


def hand_points(hands):
    """hands.HandFinder.find() output -> list of (21, 2) arrays (hands whose landmarks were found)."""
    return [np.asarray(h['points']) for h in (hands or {}).values() if h.get('points') is not None]


class YawnDetector:
    """update(t, face, hands_fn) -> YawnState. face: head_face.FaceMeasure or None. hands_fn: no-argument callable
    returning a list of (N, 2) hand point arrays in frame pixels; it is only called when needed (it is slow)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.opens = deque()                 # times of wide-open frames with the hand away
        self.open_since = None               # start of the current run of wide-open (hand away) frames
        self.pending_until = None            # a covering hand before this time confirms a yawn
        self.active_since = self.last_support = None
        self.lips = self.face_w = self.lips_t = None
        self.prev_t = None

    def _covered(self, t, hands_fn):
        if self.lips is None or t - self.lips_t > LIPS_MAX_AGE_S or hands_fn is None:
            return None
        pts = [p for p in hands_fn() if p is not None and len(p)]
        if not pts:
            return False
        lips = np.asarray(self.lips)
        return min(float(np.min(np.hypot(*(p - lips).T))) for p in pts) / self.face_w <= LIPS_DIST

    def update(self, t, face, hands_fn=None) -> YawnState:
        if self.prev_t is not None and t < self.prev_t:
            self.reset()                                         # seek / new clip
        self.prev_t = t
        jaw = None
        if face is not None and getattr(face, 'jaw_open', None) is not None and face.lips is not None and face.face_w:
            jaw, self.lips, self.face_w, self.lips_t = face.jaw_open, face.lips, face.face_w, t
        wide = jaw is not None and jaw >= JAW_OPEN
        need_hand = wide or self.active_since is not None or (self.pending_until is not None and t <= self.pending_until)
        covered = self._covered(t, hands_fn) if need_hand else None

        if wide and not covered:
            self.opens.append(t)
            self.open_since = t if self.open_since is None else self.open_since
        elif jaw is not None:                                    # face seen, mouth not wide open (or covered)
            self.open_since = None
        while self.opens and t - self.opens[0] > OPEN_WINDOW_S:
            self.opens.popleft()

        if self.active_since is None:
            if len(self.opens) >= OPEN_FRAMES:
                self.pending_until = t + COVER_WITHIN_S
            if covered and self.pending_until is not None and t <= self.pending_until:
                return self._start(t, jaw, covered, 'mouth opened wide, then a hand covered it')
            if self.open_since is not None and t - self.open_since >= UNCOVERED_S and len(self.opens) >= OPEN_FRAMES:
                return self._start(t, jaw, covered, 'mouth wide open (no hand)')
            if self.pending_until is not None and t > self.pending_until:
                self.pending_until = None
            return YawnState('none', 'mouth opened, waiting for a hand' if self.pending_until else '', jaw, covered)

        if covered or wide:
            self.last_support = t
        if t - self.last_support > END_S or t - self.active_since > MAX_S:
            started = self.active_since
            self.active_since = self.last_support = self.pending_until = None
            self.opens.clear()
            self.open_since = None
            return YawnState('none', 'yawn ended', jaw, covered, started)
        return YawnState('yawn', 'yawning', jaw, covered, self.active_since)

    def _start(self, t, jaw, covered, reason):
        self.active_since = self.last_support = t
        self.pending_until = None
        return YawnState('yawn', reason, jaw, covered, t)

    @staticmethod
    def raw_bit(state: YawnState) -> int:
        return 1 if state.state == 'yawn' else 0
