"""Find the steering wheel using temporal consensus of segmentation masks.
Usage: python3 auto_wheel.py my_video.mp4 [out/wheel.json] [--prior-fallback]
Live use: WheelCalibrator (bottom of this file) consumes frames one by one.
The wheel is searched only in the bottom-right region of the frame (ROI_*), candidates are gated
by geometry rather than by a high confidence, the vote is fitted with an ellipse, and the result
is checked against a prior ellipse. --prior-fallback (always on for WheelCalibrator) uses that prior
when detection fails; the saved source then says so ('auto_wheel_prior') and is never a detection.
Only pixels supported by a strict majority of reliable samples define the zone.
No bounding-box fallback or convex hull is applied to the wheel shape.
A separate, visible wrist tolerance is saved for pose-based hand checks.
Always inspect wheel_preview.jpg before trusting a calibration.
"""
import cv2, json, os, sys, numpy as np

PROMPTS = ['steering wheel', 'car steering wheel']
N_FRAMES = 16
KEEP_FRAC = 0.4
CONF = 0.01                  # proposal threshold; final samples must also pass MIN_CONF
MIN_CONF = 0.15
MIN_SAMPLES = 3
AREA_MIN, AREA_MAX = 0.01, 0.10
MIN_IOU = 0.25
ROI_X0, ROI_Y0 = 0.45, 0.45  # wheel is assumed to lie in the bottom-right region (fraction of frame)
ROI_MIN_CONF = 0.05          # lower than MIN_CONF because geometry gating also rejects wrong masks
CENTER_X, CENTER_Y = (0.62, 0.90), (0.72, 0.97)   # allowed mask centroid (fraction of frame)
ROI_AREA = (0.04, 0.45)      # mask area as a fraction of the ROI crop
ROI_MAX_BOX = 0.9            # box wider/taller than this fraction of the crop is not a wheel
ROI_MIN_SOLIDITY = 0.35
ELLIPSE_MIN_IOU = 0.6        # fitted ellipse must explain the voted mask
PRIOR_MIN_IOU = 0.5          # detection must overlap the prior ellipse at least this much
# Prior wheel ellipse (median of three verified clips of this camera), as centre/axes/angle fractions.
PRIOR = dict(center=(0.7583, 0.8509), axes=(0.1208, 0.2750), angle=47.0)   # axes as fraction of W
WARMUP_FRAMES = 8
DEVICE = None                # e.g. 'mps' / 0 / 'cpu'; None lets ultralytics choose
WRIST_MARGIN_FRAC = 0.15     # fraction of the wheel's shorter box dimension, not image width


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / float((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter + 1e-9)


def consensus_polygon(masks):
    if len(masks) < MIN_SAMPLES:
        raise ValueError(f'Wheel calibration needs at least {MIN_SAMPLES} reliable segmented frames.')
    shape = masks[0].shape
    if len(shape) != 2 or any(m.shape != shape for m in masks):
        raise ValueError('Wheel masks must be two-dimensional and have matching dimensions.')
    votes = np.zeros(shape, np.uint16)
    for mask in masks:
        votes += (mask != 0).astype(np.uint16)
    zone = (votes > len(masks) / 2).astype(np.uint8)
    contours, _ = cv2.findContours(zone, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError('Wheel masks have no majority agreement. Recalibrate with a clearer video.')
    contour = max(contours, key=cv2.contourArea)
    polygon = cv2.approxPolyDP(contour, 0.002 * cv2.arcLength(contour, True), True)[:, 0, :]
    if len(polygon) < 3 or cv2.contourArea(polygon) <= 0:
        raise ValueError('Wheel consensus has no usable area. Recalibrate with a clearer video.')
    return polygon


def wheel_polygon(candidates, min_conf=None):
    min_conf = MIN_CONF if min_conf is None else min_conf
    reliable = [c for c in candidates if c[0] >= min_conf]
    if len(reliable) < MIN_SAMPLES:
        raise ValueError(f'Need at least {MIN_SAMPLES} wheel masks with confidence >= {min_conf}.')
    ref = np.median(np.array([c[2] for c in reliable]), axis=0)
    consistent = [c for c in reliable if iou(c[2], ref) >= MIN_IOU]
    if not consistent:
        raise ValueError('Wheel detections disagree between frames. Recalibrate with a clearer video.')
    best = max(c[0] for c in consistent)
    used = [c for c in consistent if c[0] >= KEEP_FRAC * best]
    return consensus_polygon([c[1] for c in used]), used


def load_model():
    from ultralytics import YOLOE
    model = YOLOE('yoloe-11m-seg.pt')
    model.set_classes(PROMPTS, model.get_text_pe(PROMPTS))
    return model


def roi_origin(w, h):
    return int(ROI_X0 * w), int(ROI_Y0 * h)


def wheel_candidates(model, frame):
    """Segment the bottom-right ROI at near-native resolution; keep geometrically plausible masks."""
    h, w = frame.shape[:2]
    x0, y0 = roi_origin(w, h)
    crop = frame[y0:, x0:]
    ch, cw = crop.shape[:2]
    result = model.predict(crop, imgsz=960, conf=CONF, verbose=False, device=DEVICE)[0]
    found = []
    if result.masks is None:
        return found
    for i, box in enumerate(result.boxes):
        if i >= len(result.masks.xy) or len(result.masks.xy[i]) < 3:
            continue
        mask = np.zeros((ch, cw), np.uint8)
        cv2.fillPoly(mask, [result.masks.xy[i].astype(np.int32)], 1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        hull = cv2.contourArea(cv2.convexHull(contour))
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        ys, xs = np.nonzero(mask)
        cx, cy = (xs.mean() + x0) / w, (ys.mean() + y0) / h
        if (ROI_AREA[0] <= mask.sum() / (cw * ch) <= ROI_AREA[1]
                and (x2 - x1) / cw < ROI_MAX_BOX and (y2 - y1) / ch < ROI_MAX_BOX
                and hull > 0 and cv2.contourArea(contour) / hull >= ROI_MIN_SOLIDITY
                and CENTER_X[0] <= cx <= CENTER_X[1] and CENTER_Y[0] <= cy <= CENTER_Y[1]):
            full = np.zeros((h, w), np.uint8)
            full[y0:, x0:] = mask
            found.append((float(box.conf.item()), full, [x1 + x0, y1 + y0, x2 + x0, y2 + y0]))
    return found


def best_candidate(candidates):
    return max(candidates, key=lambda c: c[0]) if candidates else None


def ellipse_polygon(center, axes, angle, step=5):
    return cv2.ellipse2Poly((int(round(center[0])), int(round(center[1]))),
                            (int(round(axes[0] / 2)), int(round(axes[1] / 2))),
                            int(round(angle)), 0, 360, step)


def prior_polygon(frame_size):
    w, h = frame_size
    return ellipse_polygon((PRIOR['center'][0] * w, PRIOR['center'][1] * h),
                           (PRIOR['axes'][0] * w, PRIOR['axes'][1] * w), PRIOR['angle'])


def polygon_iou(a, b, frame_size):
    w, h = frame_size
    ma, mb = np.zeros((h, w), np.uint8), np.zeros((h, w), np.uint8)
    cv2.fillPoly(ma, [np.asarray(a, np.int32)], 1)
    cv2.fillPoly(mb, [np.asarray(b, np.int32)], 1)
    union = np.logical_or(ma, mb).sum()
    return float(np.logical_and(ma, mb).sum() / union) if union else 0.0


def fit_wheel_ellipse(polygon, frame_size):
    """Replace the ragged voted outline (arm/hand bumps) by its best-fit ellipse, if it fits well."""
    if len(polygon) < 5:
        raise ValueError('Too few points to fit a wheel ellipse.')
    center, axes, angle = cv2.fitEllipse(np.asarray(polygon, np.float32))
    fitted = ellipse_polygon(center, axes, angle)
    iou = polygon_iou(polygon, fitted, frame_size)
    if iou < ELLIPSE_MIN_IOU:
        raise ValueError(f'Wheel mask is not ellipse-like (IoU {iou:.2f} < {ELLIPSE_MIN_IOU}).')
    return fitted, iou


def resolve_wheel(candidates, frame_size, prior_fallback=False):
    """Candidates (one per frame) -> (polygon, info). info['source'] is 'detected' or 'prior'."""
    try:
        voted, used = wheel_polygon(candidates, min_conf=ROI_MIN_CONF)
        fitted, fit_iou = fit_wheel_ellipse(voted, frame_size)
        prior_iou = polygon_iou(fitted, prior_polygon(frame_size), frame_size)
        if prior_iou < PRIOR_MIN_IOU:
            raise ValueError(f'Detected wheel disagrees with the expected bottom-right wheel '
                             f'(IoU with prior {prior_iou:.2f} < {PRIOR_MIN_IOU}).')
        return fitted, dict(source='detected', sample_count=len(used), ellipse_iou=fit_iou,
                            prior_iou=prior_iou)
    except ValueError as error:
        if not prior_fallback:
            raise
        return prior_polygon(frame_size), dict(source='prior', sample_count=0, reason=str(error))


def tolerance_outline(wheel, margin, frame_size):
    if margin <= 0:
        return []
    w, h = frame_size
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [wheel], 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*margin+1, 2*margin+1))
    expanded = cv2.dilate(mask, kernel)
    contours, _ = cv2.findContours(expanded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contours


def wheel_margin(polygon):
    return int(round(WRIST_MARGIN_FRAC * min(np.ptp(polygon, axis=0))))


def save_calibration(polygon, info, frame_size, out, preview_frame):
    w, h = frame_size
    margin = wheel_margin(polygon)
    directory = os.path.dirname(out) or '.'
    os.makedirs(directory, exist_ok=True)
    preview = preview_frame.copy()
    outlines = tolerance_outline(polygon, margin, (w, h))
    if outlines:
        cv2.polylines(preview, outlines, True, (255, 100, 0), 3)
    cv2.polylines(preview, [polygon], True, (255, 255, 0), 4)
    label = 'PRIOR (not detected)' if info['source'] == 'prior' else 'detected'
    cv2.putText(preview, f'Cyan: wheel {label} | Blue: wrist tolerance ({margin}px)',
                (20, 50), 0, 1.1, (255, 255, 255), 2)
    preview_path = os.path.join(directory, 'wheel_preview.jpg')
    if not cv2.imwrite(preview_path, cv2.resize(preview, None, fx=0.5, fy=0.5)):
        raise OSError(f'Cannot write wheel preview: {preview_path}')
    with open(out, 'w') as f:
        json.dump({'wheel': polygon.tolist(), 'frame_size': [w, h],
                   'source': 'auto_wheel' if info['source'] == 'detected' else 'auto_wheel_prior',
                   'algorithm': 'roi_ellipse_v2', 'sample_count': info['sample_count'],
                   'wrist_margin_px': margin}, f)
    print(f'source={info["source"]}; saved {len(polygon)} points -> {out}')
    print(f'CHECK THE PREVIEW: {preview_path}')


def main(video, out, prior_fallback=False):
    cap = cv2.VideoCapture(video)
    try:
        if not cap.isOpened():
            raise ValueError(f'Cannot open video: {video}')
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if n < MIN_SAMPLES or w <= 0 or h <= 0:
            raise ValueError('Video must contain at least three frames and valid image dimensions.')
        model = load_model()
        candidates = []
        first_frame = None
        indices = (np.arange(n) if n <= N_FRAMES
                   else np.linspace(0, n - 1, N_FRAMES + 2)[1:-1].astype(int))
        for fi in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(fi))
            ok, frame = cap.read()
            if not ok:       # frame-count metadata can overstate the real length
                print(f'frame {fi}: cannot decode, skipped')
                continue
            if first_frame is None:
                first_frame = frame.copy()
            best = best_candidate(wheel_candidates(model, frame))
            if best is None:
                print(f'frame {fi}: no plausible wheel segmentation (boxes alone are not used)')
                continue
            candidates.append(best)
            print(f'frame {fi}: segmented wheel confidence {best[0]:.3f}')
    finally:
        cap.release()
    if first_frame is None:
        raise ValueError(f'No frame of {video} could be decoded.')
    polygon, info = resolve_wheel(candidates, (w, h), prior_fallback)
    if info['source'] == 'prior':
        print(f'WARNING: wheel NOT detected, using the prior ellipse. Reason: {info["reason"]}')
    save_calibration(polygon, info, (w, h), out, first_frame)


class WheelCalibrator:
    """Live wheel calibration: feed frames in order with add_frame(); no seeking or full video needed.

    status: 'collecting' -> 'locked' (polygon/margin/info set) or 'failed' (error set).
    Cost: one segmentation per fed frame (~0.2 s on Apple GPU), then nothing; a lock needs `warmup`
    frames. With prior_fallback (default) a failed detection locks on the prior ellipse and
    info['source'] == 'prior' so callers can tell it was not detected.
    """

    def __init__(self, model=None, warmup=WARMUP_FRAMES, prior_fallback=True):
        self.model = model
        self.warmup = max(warmup, MIN_SAMPLES)
        self.prior_fallback = prior_fallback
        self.status = 'collecting'
        self.polygon = self.margin = self.info = self.error = None
        self.frame_size = None
        self._candidates, self._seen = [], 0

    def add_frame(self, frame):
        if self.status != 'collecting':
            return self.status
        if self.model is None:
            self.model = load_model()
        h, w = frame.shape[:2]
        if self.frame_size not in (None, (w, h)):
            raise ValueError('Frame size changed during wheel calibration.')
        self.frame_size = (w, h)
        self._seen += 1
        best = best_candidate(wheel_candidates(self.model, frame))
        if best is not None:
            self._candidates.append(best)
        if self._seen >= self.warmup:
            try:
                self.polygon, self.info = resolve_wheel(self._candidates, self.frame_size,
                                                        self.prior_fallback)
                self.margin = wheel_margin(self.polygon)
                self.status = 'locked'
            except ValueError as error:
                self.error, self.status = str(error), 'failed'
            self._candidates = []
        return self.status


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(__doc__)
    main(args[0], args[1] if len(args) > 1 else 'out/wheel.json',
         prior_fallback='--prior-fallback' in sys.argv)
