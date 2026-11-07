"""How many people are in the car (front and rear seats). Causal: uses only detections up to the current time.

Input per analyzed frame: the person detections of the pose model (boxes xyxy, confidences, 17 keypoints each).
Output: a smoothed count per zone and in total (the second field of the CV record, see CV_ENGINE_INTERFACE.md).

Steps
  1. FILTER   keep a detection when it is confident enough, shows enough keypoints (a head point among them) and is not
              tiny. Rear-seat people are small and dim, so their thresholds are lower than the driver's.
  2. DEDUPE   the pose model often draws several overlapping boxes around one person. Two detections are the same
              person when one box mostly lies inside the other, or their head points almost coincide.
  3. ZONES    driver    the largest person whose box centre lies on the driver's side (same rule as live.py)
              front     any other person whose box is tall or reaches the bottom of the frame (front passenger)
              rear      every other person (small box, high in the frame)
              The front/rear split is a geometric rule fitted on the clips we have; it has not been validated with a
              front passenger present. Re-check it for another camera or car.
  4. SMOOTH   per zone, over the last WINDOW_S seconds: the reported count rises to k when at least RISE_FRACTION of the
              recent frames saw k people, and falls below k only when fewer than FALL_FRACTION did. A person who is
              missed for a moment (occluded by the driver, dark) therefore does not make the count flicker, and a
              one-frame ghost does not add a person.
The count is a LOWER BOUND: a person hidden by a seat, lying down or outside the camera's view is not counted.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

HEAD_KPS = (0, 1, 2, 3, 4)           # nose, eyes, ears
KP_CONF = 0.5                         # a keypoint counts as visible above this
DRIVER_ON_RIGHT = True

# ---- filter ----
MIN_CONF_DRIVER = 0.5               # the driver must be at least this confident (others: MIN_CONF_OTHER; the pose model in
                                      # live.py already drops anything below 0.25)
MIN_CONF_OTHER = 0.25
MIN_VISIBLE_KP = 5                    # of 17
MIN_BOX_HEIGHT = 0.12                 # fraction of the frame height
# ---- dedupe ----
SAME_INSIDE = 0.6                     # share of the smaller box that lies inside the other box
SAME_HEAD_DIST = 0.35                 # head points closer than this x the narrower box -> same person
SAME_INSIDE_HEAD = 0.9                # ... or one box inside the other AND the head points closer than this x the box
                                      # (a front passenger's box can overlap a rear passenger's box: heads then differ)
# ---- zones ----
FRONT_MIN_HEIGHT = 0.5                # box height / frame height
FRONT_MIN_BOTTOM = 0.85               # box bottom / frame height
# ---- smoothing ----
WINDOW_S = 3.0
RISE_FRACTION = 0.5
FALL_FRACTION = 0.25
MIN_FRAMES = 3                        # frames needed in the window before the fractions are trusted
MAX_PER_ZONE = {'driver': 1, 'front': 1, 'rear': 5}

ZONES = ('driver', 'front', 'rear')


@dataclass
class Person:
    box: np.ndarray
    conf: float
    n_kp: int
    head: Optional[np.ndarray]
    zone: str = 'rear'


@dataclass
class OccupancyObs:
    count: int                        # smoothed, all zones
    zones: dict                       # smoothed count per zone
    raw: int                          # this frame alone, after filtering and dedupe
    raw_zones: dict
    people: List[Person] = field(default_factory=list)


def _head_point(kp):
    vis = [kp[i, :2] for i in HEAD_KPS if kp[i, 2] >= KP_CONF]
    return np.mean(vis, axis=0) if vis else None


def _inside(a, b):
    """Share of the smaller box that lies inside the other one."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def find_people(boxes, confs, kps, frame_size) -> List[Person]:
    """Filtered, de-duplicated people with their zones. frame_size = (width, height)."""
    w, h = frame_size
    cands = []
    for box, conf, kp in zip(boxes, confs, kps):
        n_kp = int((kp[:, 2] >= KP_CONF).sum())
        head = _head_point(kp)
        height = (box[3] - box[1]) / h
        if conf < MIN_CONF_OTHER or n_kp < MIN_VISIBLE_KP or head is None or height < MIN_BOX_HEIGHT:
            continue
        cands.append(Person(np.asarray(box, float), float(conf), n_kp, head))
    cands.sort(key=lambda p: -p.conf)
    kept: List[Person] = []
    for p in cands:
        same = False
        for q in kept:
            wide = min(p.box[2] - p.box[0], q.box[2] - q.box[0])      # the SMALLER box: a huge driver box must not swallow a rear passenger
            head_dist = np.hypot(*(p.head - q.head)) / wide
            if head_dist < SAME_HEAD_DIST or (_inside(p.box, q.box) >= SAME_INSIDE and head_dist < SAME_INSIDE_HEAD):
                same = True
                break
        if not same:
            kept.append(p)
    # driver: largest person on the driver's side (confident enough for the driver)
    drivers = [p for p in kept if ((p.box[0] + p.box[2]) / 2 > w / 2) == DRIVER_ON_RIGHT and p.conf >= MIN_CONF_DRIVER]
    driver = max(drivers, key=lambda p: (p.box[2] - p.box[0]) * (p.box[3] - p.box[1])) if drivers else None
    for p in kept:
        if p is driver:
            p.zone = 'driver'
        elif (p.box[3] - p.box[1]) / h >= FRONT_MIN_HEIGHT or p.box[3] / h >= FRONT_MIN_BOTTOM:
            p.zone = 'front'
        else:
            p.zone = 'rear'
    return [p for p in kept if p.zone != 'rear' or p.conf >= MIN_CONF_OTHER]


class _ZoneSmoother:
    def __init__(self, cap):
        self.cap = cap
        self.hist = deque()           # (t, raw count)
        self.value = 0

    def update(self, t, raw):
        self.hist.append((t, min(raw, self.cap)))
        while self.hist and t - self.hist[0][0] > WINDOW_S:
            self.hist.popleft()
        counts = np.array([c for _, c in self.hist])
        if len(counts) < MIN_FRAMES:
            self.value = max(self.value, int(counts.max()))      # not enough history: do not hide a clear detection
            return self.value
        new = 0
        for k in range(1, self.cap + 1):
            frac = float((counts >= k).mean())
            if frac >= (FALL_FRACTION if self.value >= k else RISE_FRACTION):
                new = k
        self.value = new
        return self.value


class OccupancyCounter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.smooth = {z: _ZoneSmoother(MAX_PER_ZONE[z]) for z in ZONES}

    def update(self, t, boxes, confs, kps, frame_size) -> OccupancyObs:
        people = find_people(boxes, confs, kps, frame_size) if len(boxes) else []
        raw_zones = {z: sum(p.zone == z for p in people) for z in ZONES}
        zones = {z: self.smooth[z].update(t, raw_zones[z]) for z in ZONES}
        return OccupancyObs(sum(zones.values()), zones, len(people), raw_zones, people)
