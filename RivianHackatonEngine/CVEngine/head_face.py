"""Face measurement for the head rule: MediaPipe FaceLandmarker on a tight crop around the head.

Gives what the 2D pose keypoints cannot: head yaw / pitch / roll in DEGREES (from the facial transformation matrix),
how far the eyes look down / up, and an eye-closure (blink) score. The closure score is recorded for the future sleep
detection; it is not used by the off-road rule.

Angles are relative to the CAMERA, and this cabin camera sees the driver from the side, so 'looking straight ahead'
is not 0 degrees and differs per driver (measured forward yaw +5 to +33 deg across 8 drivers). The rule therefore works
on the change from the driver's own baseline (see eyes_off_road.py).
Sign convention (checked on real frames): yaw negative = head turned toward the LEFT of the image, pitch positive =
head down. Cost about 9 ms per call; it found a face in 81 to 98% of analyzed frames, bright and dark footage alike.
Where it finds nothing the caller falls back to the pose keypoints.
"""
import os, urllib.request
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'face_landmarker.task')
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/'
             'float16/latest/face_landmarker.task')
NOSE, LEYE, REYE = 0, 1, 2          # pose keypoints used to place the crop
CROP_PER_EYE_DISTANCE = 3.4         # crop half-size = this * eye distance (px), clamped below
CROP_MIN, CROP_MAX, CROP_DEFAULT = 120, 260, 170
KP_CONF = 0.3                       # the nose must be at least this confident to place a crop


@dataclass
class FaceMeasure:
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    gaze_down: float                # 0..1 average of eyeLookDownLeft/Right
    gaze_up: float                  # 0..1 average of eyeLookUpLeft/Right
    blink: float                    # 0..1 average of eyeBlinkLeft/Right; near 1 = eyes closed
    ear_l: Optional[float] = None   # eye aspect ratio from the eyelid landmarks (about 0.25-0.4 open, <0.1 closed)
    ear_r: Optional[float] = None


def ensure_model(path=MODEL_PATH):
    if not os.path.isfile(path):
        print(f'Downloading the MediaPipe face model to {path} ...', flush=True)
        urllib.request.urlretrieve(MODEL_URL, path)
    return path


def load_landmarker(path=None):
    from mediapipe.tasks.python import vision, BaseOptions
    options = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=ensure_model(path or MODEL_PATH)), num_faces=1,
        output_face_blendshapes=True, output_facial_transformation_matrixes=True,
        min_face_detection_confidence=0.2, min_face_presence_confidence=0.2)
    return vision.FaceLandmarker.create_from_options(options)


# MediaPipe face-mesh eyelid points: corner, upper lid x2, corner, lower lid x2 (EAR = lid gaps / eye width)
EYE_R = (33, 160, 158, 133, 153, 144)
EYE_L = (362, 385, 387, 263, 373, 380)


def eye_aspect_ratio(pts, idx):
    """pts: (N,2+) landmark pixels. Geometric eye openness: independent of the blendshape model, so the two can
    confirm each other. Scale-free, but it depends on the view angle, so the caller normalises per driver."""
    d = lambda a, b: float(np.linalg.norm(np.asarray(pts[idx[a]][:2]) - np.asarray(pts[idx[b]][:2])))
    width = d(0, 3)
    return (d(1, 5) + d(2, 4)) / (2 * width) if width > 1e-6 else None


def euler_from_matrix(matrix):
    """(yaw, pitch, roll) in degrees from the 3x3 rotation block of the facial transformation matrix."""
    m = np.asarray(matrix, float)[:3, :3]
    sy = np.sqrt(m[0, 0] ** 2 + m[1, 0] ** 2)
    return (float(np.degrees(np.arctan2(-m[2, 0], sy))), float(np.degrees(np.arctan2(m[2, 1], m[2, 2]))),
            float(np.degrees(np.arctan2(m[1, 0], m[0, 0]))))


def crop_box(kp, frame_shape):
    """Square crop (x0, y0, x1, y1) around the head from the pose keypoints, or None when the nose is not found."""
    if kp is None or kp[NOSE, 2] < KP_CONF or np.isnan(kp[NOSE, 0]):
        return None
    h, w = frame_shape[:2]
    eye_distance = abs(float(kp[LEYE, 0] - kp[REYE, 0])) if min(kp[LEYE, 2], kp[REYE, 2]) >= KP_CONF else np.nan
    half = int(np.clip(CROP_PER_EYE_DISTANCE * eye_distance, CROP_MIN, CROP_MAX)) if eye_distance == eye_distance \
        else CROP_DEFAULT
    x, y = int(kp[NOSE, 0]), int(kp[NOSE, 1])
    return max(x - half, 0), max(y - half, 0), min(x + half, w), min(y + half, h)


class FaceHead:
    """measure(frame, kp) -> FaceMeasure, or None when there is no head location or no face is found."""

    def __init__(self, landmarker=None):
        self.landmarker = landmarker or load_landmarker()

    def _detect(self, rgb):
        import mediapipe as mp
        return self.landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))

    def measure(self, frame, kp) -> Optional[FaceMeasure]:
        box = crop_box(kp, frame.shape)
        if box is None:
            return None
        x0, y0, x1, y1 = box
        if x1 - x0 < 32 or y1 - y0 < 32:
            return None
        result = self._detect(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2RGB))
        if not result.face_landmarks or not result.facial_transformation_matrixes:
            return None
        yaw, pitch, roll = euler_from_matrix(result.facial_transformation_matrixes[0])
        scores = {c.category_name: c.score for c in (result.face_blendshapes[0] if result.face_blendshapes else [])}
        mean = lambda a, b: (scores.get(a, 0.0) + scores.get(b, 0.0)) / 2
        h, w = y1 - y0, x1 - x0
        try:                                  # the eye openness is extra evidence: never let it break the head angles
            pts = [(l.x * w, l.y * h) for l in result.face_landmarks[0]]
            ear_l, ear_r = (eye_aspect_ratio(pts, EYE_L), eye_aspect_ratio(pts, EYE_R)) if len(pts) > 380 \
                else (None, None)
        except (TypeError, AttributeError, IndexError):
            ear_l = ear_r = None
        return FaceMeasure(yaw, pitch, roll, mean('eyeLookDownLeft', 'eyeLookDownRight'),
                           mean('eyeLookUpLeft', 'eyeLookUpRight'), mean('eyeBlinkLeft', 'eyeBlinkRight'),
                           ear_l, ear_r)
