"""Hand-on-wheel method 3/3: HYBRID (default for live.py). MediaPipe landmarks where it finds the hand,
pose proxy to fill its blind spots.
Per hand:
  MediaPipe found the hand  -> trust it for both 'near' and 'away' (it separates a hand in the lap from one on the rim)
  MediaPipe found nothing   -> pose proxy 'near' stays 'near'; pose proxy 'away' becomes 'unknown'
The last rule is deliberate: the proxy's 'away' is its unreliable direction (a hand gripping the outer rim reads
away), and 'unknown' can never raise a hands-off alert. Same interface: update(frame, kp) -> (states, debug).
"""
import hands_pose, hands_mediapipe


def combine(mediapipe_state, pose_state):
    """-> (state, source)"""
    if mediapipe_state in ('near', 'away'):
        return mediapipe_state, 'mediapipe'
    if pose_state == 'near':
        return 'near', 'pose'
    return 'unknown', 'none'


class HybridHands:
    name = 'hybrid'

    def __init__(self, wheel, margin, pose=None, mediapipe=None):
        self.pose = pose or hands_pose.PoseHands(wheel, margin)
        self.mediapipe = mediapipe or hands_mediapipe.MediaPipeHands(wheel, margin)

    def update(self, frame, kp):
        pose_states, pose_debug = self.pose.update(frame, kp)
        mp_states, mp_debug = self.mediapipe.update(frame, kp)
        states, sources = [], []
        for ms, ps in zip(mp_states, pose_states):
            state, source = combine(ms, ps)
            states.append(state)
            sources.append(source)
        return states, dict(points=pose_debug['points'], landmarks=mp_debug['landmarks'], sources=sources)
