"""Hand-on-wheel method 2/3: MEDIAPIPE hand landmarks.
A tight window around each pose-estimated hand is sent to MediaPipe HandLandmarker (a hand is too small for it in
the full fisheye frame). A hand is 'near' when at least MIN_IN of its 21 landmarks lie in the wheel zone (plus
PT_MARGIN px), 'away' when it is found but fewer do, and 'unknown' when no hand is found in the window.
Strong when it sees the hand; it often cannot see a hand gripping the rim, so on its own it returns many 'unknown'.
Same interface as hands_pose.py: update(frame, kp) -> (states, debug).
"""
import os, urllib.request
import cv2, numpy as np
import hands_pose

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hand_landmarker.task')
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/'
             'float16/latest/hand_landmarker.task')
WIN = 400            # window (px) around the pose hand point
PT_MARGIN = 12       # px a landmark may lie outside the wheel polygon and still count as on it
MIN_IN = 3           # landmarks (of 21) that must be on the wheel for 'near'
MAX_OFFSET = 0.45    # a found hand must be within this fraction of WIN from the pose hand point
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
               (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


def ensure_model(path=MODEL_PATH):
    if not os.path.isfile(path):
        print(f'Downloading the MediaPipe hand model to {path} ...', flush=True)
        urllib.request.urlretrieve(MODEL_URL, path)
    return path


def load_landmarker(path=None):
    import mediapipe as mp
    from mediapipe.tasks.python import vision, BaseOptions
    options = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=ensure_model(path or MODEL_PATH)), num_hands=2,
        min_hand_detection_confidence=0.2, min_hand_presence_confidence=0.2)
    return vision.HandLandmarker.create_from_options(options)


class MediaPipeHands:
    name = 'mediapipe'

    def __init__(self, wheel, margin=None, landmarker=None):
        self.wheel = np.asarray(wheel, np.int32).reshape(-1, 1, 2)
        self.landmarker = landmarker or load_landmarker()

    def _detect(self, rgb):
        import mediapipe as mp
        result = self.landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                 data=np.ascontiguousarray(rgb)))
        return result.hand_landmarks

    def update(self, frame, kp):
        h, w = frame.shape[:2]
        win = min(WIN, w, h)
        points = hands_pose.hand_points(kp)
        states, landmarks, sources = [], [], []
        for p in points:
            if p is None:
                states.append('unknown'); landmarks.append(None); sources.append('none')
                continue
            cx, cy = float(p[1][0]), float(p[1][1])
            x0 = int(min(max(cx - win / 2, 0), w - win))
            y0 = int(min(max(cy - win / 2, 0), h - win))
            found = self._detect(cv2.cvtColor(frame[y0:y0 + win, x0:x0 + win], cv2.COLOR_BGR2RGB))
            best = None
            for lm in found:
                pts = np.array([[x0 + q.x * win, y0 + q.y * win] for q in lm])
                d = float(np.hypot(*(pts.mean(0) - [cx, cy])))
                if best is None or d < best[0]:
                    best = (d, pts)
            if best is None or best[0] > win * MAX_OFFSET:
                states.append('unknown'); landmarks.append(None); sources.append('none')
                continue
            inside = np.array([cv2.pointPolygonTest(self.wheel, (float(q[0]), float(q[1])), True) >= -PT_MARGIN
                               for q in best[1]])
            states.append('near' if int(inside.sum()) >= MIN_IN else 'away')
            landmarks.append((best[1], inside))
            sources.append('mediapipe')
        return states, dict(points=points, landmarks=landmarks, sources=sources)
