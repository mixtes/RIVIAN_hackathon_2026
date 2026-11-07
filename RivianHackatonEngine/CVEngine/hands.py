"""Driver hand landmarks (MediaPipe Hand Landmarker) and hand-based decisions.

Body pose gives only wrists. A hand resting on the rim often has its wrist outside the wheel zone,
which caused false "hands off" results. Here each visible driver wrist gets a hand-landmark search
in an enlarged crop around it (21 points per hand, fingers and palm), mapped back to the frame.

- hand_on_wheel: ON when >= 2 fingertips are on the calibrated wheel zone; OFF when the hand is
  found elsewhere, or not found while the wrist is far from the wheel; otherwise UNKNOWN.
- hands_off_evidence: HANDS_OFF_WHEEL evidence is POSITIVE only when both hands are clearly OFF;
  any hand ON gives NEGATIVE; anything else is UNKNOWN (never guessed).
- fingers_on_phone: how many hand points lie on a phone box (a held phone, not one lying nearby).

MediaPipe is installed without its opencv-contrib dependency (see requirements-hands.txt).
The model file is downloaded once to hand_landmarker.task next to this script.
"""
import os
import urllib.request

import cv2
import numpy as np

from phone_events import (Evidence, KP_CONF, LSHO, LWR, NEGATIVE, POSITIVE, RSHO, RWR, UNKNOWN, _scale,
                          fingers_on_phone)  # noqa: F401  (re-exported)

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/'
             'float16/latest/hand_landmarker.task')
MODEL_PATH = os.path.join(HERE, 'hand_landmarker.task')
LEL, REL = 7, 8
CROP_HALF = 0.8            # search area: this many shoulder widths around the wrist
CROP_SIZE = 512            # the crop is enlarged to this size; small hands are missed otherwise
MAX_WRIST_OFFSET = 0.5     # the found hand's wrist point must be this close to the pose wrist (shoulder widths)
TIPS = (4, 8, 12, 16, 20)
TIP_TOLERANCE_PX = 10      # a fingertip this close outside the wheel zone still counts as on it
MIN_TIPS_ON_WHEEL = 2
FAR_FROM_WHEEL = 0.6       # wrist this far outside the wheel zone (shoulder widths) is OFF even without landmarks
ON, OFF = 'ON', 'OFF'


def ensure_model(path=MODEL_PATH):
    if not os.path.isfile(path):
        print(f'downloading hand landmark model -> {path}')
        urllib.request.urlretrieve(MODEL_URL, path)
    return path


class HandFinder:
    """Finds the landmarks of each visible driver wrist's hand. `detect` may be injected for tests:
    it takes an RGB crop and returns a list of 21 (x, y) points normalized to the crop, or None."""

    def __init__(self, detect=None, model_path=MODEL_PATH, min_confidence=0.1):
        if detect is None:
            import mediapipe as mp
            from mediapipe.tasks import python as mpt
            from mediapipe.tasks.python import vision
            landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                base_options=mpt.BaseOptions(model_asset_path=ensure_model(model_path)),
                running_mode=vision.RunningMode.IMAGE, num_hands=1,
                min_hand_detection_confidence=min_confidence, min_hand_presence_confidence=min_confidence,
                min_tracking_confidence=min_confidence))

            def detect(rgb):
                res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
                return [(l.x, l.y) for l in res.hand_landmarks[0]] if res.hand_landmarks else None
        self.detect = detect

    def find(self, frame, kp):
        """{'L'/'R': {'wrist': (x, y), 'points': (21, 2) array or None}} for each confidently visible wrist."""
        if kp is None:
            return {}
        h, w = frame.shape[:2]
        sw = _scale(kp)
        hands = {}
        for side, wr, el in (('L', LWR, LEL), ('R', RWR, REL)):
            if kp[wr, 2] < KP_CONF:
                continue
            wrist = kp[wr, :2].astype(float)
            half = int(CROP_HALF * sw)
            x0, y0 = int(max(0, wrist[0] - half)), int(max(0, wrist[1] - half))
            x1, y1 = int(min(w, wrist[0] + half)), int(min(h, wrist[1] + half))
            points = None
            if x1 - x0 > 8 and y1 - y0 > 8:
                crop = cv2.resize(frame[y0:y1, x0:x1], (CROP_SIZE, CROP_SIZE))
                found = self.detect(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                if found:
                    pts = np.array([(x0 + px * (x1 - x0), y0 + py * (y1 - y0)) for px, py in found])
                    if np.hypot(*(pts[0] - wrist)) <= MAX_WRIST_OFFSET * sw:     # it is this wrist's hand
                        points = pts
            hands[side] = dict(wrist=tuple(wrist), points=points)
        return hands


def hand_on_wheel(hand, wheel, sw):
    contour = np.asarray(wheel, np.int32).reshape(-1, 1, 2)
    if hand['points'] is not None:
        tips = sum(cv2.pointPolygonTest(contour, tuple(map(float, hand['points'][i])), True) >= -TIP_TOLERANCE_PX
                   for i in TIPS)
        return ON if tips >= MIN_TIPS_ON_WHEEL else OFF
    outside = -cv2.pointPolygonTest(contour, tuple(map(float, hand['wrist'])), True)
    return OFF if outside > FAR_FROM_WHEEL * sw else UNKNOWN


def hands_off_evidence(ts, kp, hands, wheel):
    """HANDS_OFF_WHEEL evidence for one frame."""
    if kp is None or wheel is None:
        return Evidence(ts, UNKNOWN, 'no driver' if kp is None else 'no wheel calibration'), {}
    sw = _scale(kp)
    states = {side: hand_on_wheel(h, wheel, sw) for side, h in hands.items()}
    if ON in states.values():
        return Evidence(ts, NEGATIVE, 'hand on wheel'), states
    if len(states) == 2 and all(s == OFF for s in states.values()):
        return Evidence(ts, POSITIVE, 'both hands off wheel'), states
    return Evidence(ts, UNKNOWN, 'hand position not clear'), states
