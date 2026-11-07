"""Phone-use decisions: per-frame evidence and confirmed PHONE_USE events.

One public behavior, PHONE_USE, covers a phone at the ear and a phone used in the hand.
The goal is few false alarms, so evidence is only POSITIVE when it is clear:

- At the ear: a phone box near the ear/nose AND a wrist near that phone (someone holds it there).
  A box near the head without a hand (e.g. on a headrest) is not enough.
- In the hand: a wrist near the phone AND either the phone moves relative to the cabin over the last
  seconds, or the driver looks down at it (head tilted down) with the fingers on the phone (hand
  landmarks; looking at the dash with a hand on the wheel next to a lying phone is not use) and a
  confident phone box. A phone
  lying in the tray stays still relative to the cabin even when the camera shakes, so with the head up
  it is rejected even if a hand rests next to it. Until enough history exists the frame is UNKNOWN.
- No driver visible, or no usable wrist next to a detected phone: UNKNOWN (never "no phone").

EventTracker (PhoneUseTracker for PHONE_USE) turns per-frame evidence into events: an event starts when
most analyzed frames in the last ~2 s are POSITIVE, and ends after ~3 s without POSITIVE frames (with at
least one clear NEGATIVE), when the driver is not observable for too long, or at the end of the clip.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

import cv2
import numpy as np

POSITIVE, NEGATIVE, UNKNOWN = 'POSITIVE', 'NEGATIVE', 'UNKNOWN'
ACTION = 'PHONE_USE'

KP_CONF = 0.35
NOSE, LEAR, REAR, LSHO, RSHO, LEL, REL, LWR, RWR = 0, 3, 4, 5, 6, 7, 8, 9, 10
DEFAULT_SCALE = 350.0          # shoulder width in px when shoulders are not visible
EAR_DIST = 0.8                 # phone center to ear/nose, in shoulder widths
EAR_HAND_DIST = 0.8            # wrist to phone center for a phone held at the ear
HAND_DIST = 0.6                # wrist to phone center for a phone in the hand
HAND_EDGE_DIST = 0.35          # ...or wrist to the nearest edge of the phone box (big phones held flat)
SAME_PHONE_DIST = 0.5          # max jump of a phone between analyzed frames to count as the same phone
STILL_WINDOW_S = 3.0           # look back this long to decide whether the phone moves
STILL_MIN_SPAN_S = 1.5         # need observations spread over at least this long
STILL_MIN_OBS = 3
STILL_RANGE = 0.08             # phone moved less than this (shoulder widths, cabin-relative) -> lying still
LOOK_DOWN_PITCH = 0.17         # (nose y - ear y) / shoulder width above this -> driver looks down at the phone
MIN_FINGERS_ON_PHONE = 3       # hand points on the phone box needed for a still phone to count as held
MIN_STILL_CONF = 0.3           # a still phone (no motion cue) also needs this detector confidence;
                               # weaker boxes were e.g. the textured grip pad of the steering wheel
PALM_ALONG = (0.35, 0.75)      # phone in the palm: center this far past the wrist along the forearm (shoulder widths)
PALM_NEAR_EDGE = 0.4           # for a big phone: its near edge at most this far past the wrist
PALM_ACROSS = 0.33             # ...and at most this far to the side of the forearm line
PALM_SLOPE = 0.8               # ...and at most ~39 degrees off the forearm direction
MIN_PALM_CONF = 0.15           # detector confidence needed for the in-palm check

WINDOW_S = 2.0                 # confirmation window
START_MIN_POSITIVE = 3
START_MIN_RATIO = 0.6          # POSITIVE / (POSITIVE + NEGATIVE) inside the window
START_MIN_SPAN_S = 1.0         # positives must be spread over at least this long
END_GAP_S = 3.0                # end after this long without POSITIVE (and at least one NEGATIVE);
                               # shorter gaps are usually missed detections, not the end of phone use
LOST_AFTER_S = 3.0             # end with observation_lost after this long with only UNKNOWN


@dataclass
class Evidence:
    timestamp: float
    state: str
    reason: str
    phone: Optional[tuple] = None      # (x1, y1, x2, y2, conf) in full-frame pixels


class CabinMotion:
    """Estimates how far the whole image shifted relative to the first frame (camera shake),
    using phase correlation on a region that is fixed to the car (the roof liner at the top)."""

    def __init__(self, region=None, min_response=0.05):
        """region: (row slice, col slice); None = the roof-liner area of the 1920x1536 cabin camera,
        scaled to the actual frame size."""
        self.region, self.min_response, self.ref, self.window = region, min_response, None, None

    def shift(self, frame):
        region = self.region
        if region is None:
            h, w = frame.shape[:2]
            region = (slice(int(40 * h / 1536), int(260 * h / 1536)), slice(int(500 * w / 1920), int(1400 * w / 1920)))
        patch = cv2.cvtColor(frame[region], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if self.ref is None or patch.shape != self.ref.shape:
            self.ref = patch
            self.window = cv2.createHanningWindow(patch.shape[::-1], cv2.CV_32F)
            return 0.0, 0.0
        (dx, dy), response = cv2.phaseCorrelate(self.ref, patch, self.window)
        return (dx, dy) if response >= self.min_response else (0.0, 0.0)


def _scale(kp):
    ok = lambda i: kp[i, 2] >= KP_CONF
    return float(np.hypot(*(kp[LSHO, :2] - kp[RSHO, :2]))) if ok(LSHO) and ok(RSHO) else DEFAULT_SCALE


def looking_down(kp, sw):
    """True when the nose is clearly below the ears (head tilted down), None when not measurable."""
    ok = lambda i: kp[i, 2] >= KP_CONF
    ears = [kp[i, 1] for i in (LEAR, REAR) if ok(i)]
    if not ears or not ok(NOSE):
        return None
    return (kp[NOSE, 1] - float(np.mean(ears))) / sw > LOOK_DOWN_PITCH


class PhoneEvidence:
    """Per-frame evidence with the short phone-position history needed for the stillness check."""

    def __init__(self):
        self.history = deque()              # (timestamp, cabin-relative center)

    def reset(self):
        self.history.clear()

    def _moving(self, ts, center, sw):
        """True / False / None (not enough history) for 'this phone moves relative to the cabin'."""
        while self.history and ts - self.history[0][0] > STILL_WINDOW_S:
            self.history.popleft()
        same = [c for t, c in self.history if np.hypot(*(c - center)) < SAME_PHONE_DIST * sw]
        times = [t for t, c in self.history if np.hypot(*(c - center)) < SAME_PHONE_DIST * sw]
        self.history.append((ts, center))
        if len(same) + 1 < STILL_MIN_OBS or ts - min(times, default=ts) < STILL_MIN_SPAN_S:
            return None
        pts = np.array(same + [center])
        return float(np.ptp(pts, axis=0).max()) >= STILL_RANGE * sw

    def update(self, ts, kp, phones, cabin_shift=(0.0, 0.0), hands=None) -> Evidence:
        """hands: optional {'L'/'R': {'wrist': (x, y), 'points': (21, 2) or None}} from hands.HandFinder,
        or a no-argument callable returning it (called only when needed, since the search is slow)."""
        if kp is None or np.isnan(kp[:, 0]).all():
            return Evidence(ts, UNKNOWN, 'no driver')
        sw = _scale(kp)
        ok = lambda i: kp[i, 2] >= KP_CONF
        head = [kp[i, :2] for i in (LEAR, REAR, NOSE) if ok(i)]
        wrists = [kp[i, :2] for i in (LWR, RWR) if ok(i)]
        if not phones:
            return Evidence(ts, NEGATIVE, 'no phone')
        if not wrists:
            return Evidence(ts, UNKNOWN, 'phone seen but no usable wrist')
        shift = np.asarray(cabin_shift, float)
        best = None
        for p in phones:
            c = np.array([(p[0] + p[2]) / 2, (p[1] + p[3]) / 2])
            hand = min(np.hypot(*(c - w)) for w in wrists) / sw
            edge = min(_box_distance(w, p) for w in wrists) / sw
            ear = min((np.hypot(*(c - h)) for h in head), default=np.inf) / sw
            if ear < EAR_DIST and hand < EAR_HAND_DIST:
                return Evidence(ts, POSITIVE, 'phone at ear, held by hand', tuple(p))
            # A big phone held flat (landscape, at the fingertips) has its center far from the wrist even
            # though its near edge is in the hand, so the box edge counts too.
            if (hand < HAND_DIST or edge < HAND_EDGE_DIST) and (best is None or edge < best[0]):
                best = (edge, p, c)
        if best is None:
            return Evidence(ts, NEGATIVE, 'phone not at ear or hand', tuple(phones[0]))
        _, p, c = best
        moving = self._moving(ts, c - shift, sw)
        if moving:
            return Evidence(ts, POSITIVE, 'phone in hand and moving', tuple(p))
        conf = p[4] if len(p) > 4 else 1.0
        palm = phone_in_palm(kp, c, sw, p)
        if looking_down(kp, sw):
            # A still phone near the hand while looking down is only use if it is held: fingers on it
            # (hand landmarks), or, when the fingers are hidden behind the phone, the phone sits in the palm
            # (straight out from the forearm). Otherwise it is e.g. a hand on the wheel next to a lying phone.
            if conf < min(MIN_STILL_CONF, MIN_PALM_CONF):
                return Evidence(ts, UNKNOWN, 'looking down, phone detection too weak', tuple(p))
            points = _hand_points_near(hands() if callable(hands) else hands, c)
            if points is not None:
                if conf >= MIN_STILL_CONF and fingers_on_phone(points, p) >= MIN_FINGERS_ON_PHONE:
                    return Evidence(ts, POSITIVE, 'phone held, driver looking at it', tuple(p))
                if not palm:
                    return Evidence(ts, NEGATIVE, 'hand not holding the phone', tuple(p))
            if palm and conf >= MIN_PALM_CONF:
                return Evidence(ts, POSITIVE, 'phone in palm, driver looking at it', tuple(p))
            return Evidence(ts, UNKNOWN, 'looking down, hand on phone not confirmed', tuple(p))
        if moving is None or palm:
            return Evidence(ts, UNKNOWN, 'phone in hand, use not clear', tuple(p))
        return Evidence(ts, NEGATIVE, 'phone near hand but lying still', tuple(p))


def _box_distance(point, box):
    """Pixels from a point to the nearest point of an (x1, y1, x2, y2, ...) box; 0 inside it."""
    dx = max(box[0] - point[0], 0.0, point[0] - box[2])
    dy = max(box[1] - point[1], 0.0, point[1] - box[3])
    return float(np.hypot(dx, dy))


def phone_in_palm(kp, center, sw, box=None):
    """True when the phone center sits where the palm is: straight out from a wrist along its forearm
    (elbow -> wrist direction). A lying phone next to a resting hand is beside or behind the wrist.
    With `box`, a big phone may reach further: its near edge (not its center) must be within reach."""
    for wr, el in ((LWR, LEL), (RWR, REL)):
        if kp[wr, 2] < KP_CONF or kp[el, 2] < KP_CONF:
            continue
        u = kp[wr, :2] - kp[el, :2]
        if np.hypot(*u) < 1e-6:
            continue
        u = u / np.hypot(*u)
        v = (np.asarray(center) - kp[wr, :2]) / sw
        along, across = float(v @ u), abs(float(u[0] * v[1] - u[1] * v[0]))
        far = PALM_ALONG[1]
        if box is not None:
            half = 0.5 * (abs(u[0]) * (box[2] - box[0]) + abs(u[1]) * (box[3] - box[1])) / sw
            far = max(far, half + PALM_NEAR_EDGE)
        if PALM_ALONG[0] <= along <= far and across <= min(PALM_ACROSS, PALM_SLOPE * along):
            return True
    return False


def fingers_on_phone(points, phone, pad=0.15):
    """Number of hand points inside the phone box, enlarged by `pad` of its size on each side."""
    if points is None:
        return 0
    x1, y1, x2, y2 = phone[:4]
    px, py = pad * (x2 - x1), pad * (y2 - y1)
    pts = np.asarray(points)
    inside = (pts[:, 0] >= x1 - px) & (pts[:, 0] <= x2 + px) & (pts[:, 1] >= y1 - py) & (pts[:, 1] <= y2 + py)
    return int(inside.sum())


def _hand_points_near(hands, center):
    """Landmarks of the hand whose wrist is closest to `center`, or None (no hands / not found)."""
    if not hands:
        return None
    side = min(hands, key=lambda s: np.hypot(*(np.asarray(hands[s]['wrist']) - center)))
    return hands[side]['points']


@dataclass
class PhoneEvent:
    event_id: int
    action: str
    onset_s: float
    confirmed_at_s: float
    last_supported_s: float
    end_s: Optional[float] = None
    end_reason: Optional[str] = None
    positive_frames: int = 0
    evidence_times: List[float] = field(default_factory=list)


class EventTracker:
    """Causal confirmation for one action: feed evidence in time order; returns ('start'|'end', event)."""

    def __init__(self, action=ACTION):
        self.action = action
        self.reset()

    def reset(self):
        self.window, self.active, self.events, self.next_id = deque(), None, [], 1
        self.last_positive = self.last_known = None
        self.negative_since_positive = False

    def update(self, ev: Evidence):
        if self.window and ev.timestamp < self.window[-1].timestamp:
            self.reset()                                        # seek or new clip
        updates = []
        self.window.append(ev)
        while self.window and ev.timestamp - self.window[0].timestamp > WINDOW_S:
            self.window.popleft()
        if ev.state != UNKNOWN:
            self.last_known = ev.timestamp
        if ev.state == POSITIVE:
            self.last_positive, self.negative_since_positive = ev.timestamp, False
        elif ev.state == NEGATIVE:
            self.negative_since_positive = True
        if self.active is None:
            pos = [e.timestamp for e in self.window if e.state == POSITIVE]
            neg = sum(e.state == NEGATIVE for e in self.window)
            if (len(pos) >= START_MIN_POSITIVE and len(pos) / (len(pos) + neg) >= START_MIN_RATIO
                    and pos[-1] - pos[0] >= START_MIN_SPAN_S):
                self.active = PhoneEvent(self.next_id, self.action, pos[0], ev.timestamp, pos[-1],
                                         positive_frames=len(pos), evidence_times=pos)
                self.next_id += 1
                updates.append(('start', self.active))
        else:
            if ev.state == POSITIVE:
                self.active.last_supported_s = ev.timestamp
                self.active.positive_frames += 1
                self.active.evidence_times.append(ev.timestamp)
            elif (self.negative_since_positive and self.last_positive is not None
                  and ev.timestamp - self.last_positive >= END_GAP_S):
                updates.append(('end', self._end(ev.timestamp, 'phone no longer in use')))
            elif self.last_known is not None and ev.timestamp - self.last_known >= LOST_AFTER_S:
                updates.append(('end', self._end(ev.timestamp, 'observation lost')))
        return updates

    def finish(self, timestamp):
        """Close an open event at the end of the clip/drive."""
        return [('end', self._end(timestamp, 'clip end'))] if self.active else []

    def _end(self, ts, reason):
        ev, self.active = self.active, None
        ev.end_s, ev.end_reason = ts, reason
        self.events.append(ev)
        return ev


PhoneUseTracker = EventTracker
