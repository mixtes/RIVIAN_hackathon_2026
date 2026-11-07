"""Hand-on-wheel method 1/3: POSE PROXY. Pose keypoints only (no extra model).
The hand centre is estimated as the wrist pushed along the forearm (rules.HAND_EXT) and tested against the
wheel zone plus its tolerance margin. Cheap, but a hand resting beside the wheel can count as near and a hand
gripping the outer rim can count as away. Image-space proximity only, not verified grip/contact.

Every method exposes the same interface:  update(frame, kp) -> (states, debug)
  states: [left, right], each 'near' | 'away' | 'unknown'
  debug : dict(points=[(wrist, hand)|None]*2, landmarks=[None|(pts21, inside_flags)]*2, sources=[str]*2)
"""
import rules

SIDES = ((rules.LWR, rules.LELB), (rules.RWR, rules.RELB))


def hand_points(kp):
    """[(wrist_xy, hand_xy) | None] for left and right; None when the wrist is not reliably seen."""
    out = []
    for wrist, elbow in SIDES:
        if kp is None or kp[wrist, 2] < rules.KP_CONF:
            out.append(None)
            continue
        w = kp[wrist, :2]
        h = w + rules.HAND_EXT * (w - kp[elbow, :2]) if kp[elbow, 2] >= rules.KP_CONF else w
        out.append((w, h))
    return out


def hand_states(kp, wheel, margin):
    points = hand_points(kp)
    states = ['unknown' if p is None else ('near' if rules.on_wheel(p[1], wheel, margin) else 'away')
              for p in points]
    return states, points


class PoseHands:
    name = 'pose'

    def __init__(self, wheel, margin):
        self.wheel, self.margin = wheel, margin

    def update(self, frame, kp):
        states, points = hand_states(kp, self.wheel, self.margin)
        sources = ['pose' if s != 'unknown' else 'none' for s in states]
        return states, dict(points=points, landmarks=[None, None], sources=sources)
