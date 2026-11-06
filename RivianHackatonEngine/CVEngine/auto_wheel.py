"""Find the steering wheel using temporal consensus of segmentation masks.
Usage: python3 auto_wheel.py my_video.mp4 [out/wheel.json]
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


def wheel_polygon(candidates):
    reliable = [c for c in candidates if c[0] >= MIN_CONF]
    if len(reliable) < MIN_SAMPLES:
        raise ValueError(f'Need at least {MIN_SAMPLES} wheel masks with confidence >= {MIN_CONF}.')
    ref = np.median(np.array([c[2] for c in reliable]), axis=0)
    consistent = [c for c in reliable if iou(c[2], ref) >= MIN_IOU]
    if not consistent:
        raise ValueError('Wheel detections disagree between frames. Recalibrate with a clearer video.')
    best = max(c[0] for c in consistent)
    used = [c for c in consistent if c[0] >= KEEP_FRAC * best]
    return consensus_polygon([c[1] for c in used]), used


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


def main(video, out):
    from ultralytics import YOLOE

    cap = cv2.VideoCapture(video)
    try:
        if not cap.isOpened():
            raise ValueError(f'Cannot open video: {video}')
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if n < MIN_SAMPLES or w <= 0 or h <= 0:
            raise ValueError('Video must contain at least three frames and valid image dimensions.')
        model = YOLOE('yoloe-11m-seg.pt')
        model.set_classes(PROMPTS, model.get_text_pe(PROMPTS))
        candidates = []
        first_frame = None
        indices = (np.arange(n) if n <= N_FRAMES
                   else np.linspace(0, n - 1, N_FRAMES + 2)[1:-1].astype(int))
        for fi in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(fi))
            ok, frame = cap.read()
            if not ok:
                raise ValueError(f'Cannot decode sampled frame {fi} in {video}.')
            if first_frame is None:
                first_frame = frame.copy()
            result = model.predict(frame, imgsz=960, conf=CONF, verbose=False)[0]
            plausible = []
            if result.masks is not None:
                for i, box in enumerate(result.boxes):
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                    area = (x2-x1) * (y2-y1) / (w*h)
                    if AREA_MIN <= area <= AREA_MAX and i < len(result.masks.xy):
                        points = result.masks.xy[i]
                        if len(points) >= 3 and cv2.contourArea(points) > 0:
                            plausible.append((float(box.conf.item()), points, [x1, y1, x2, y2]))
            if not plausible:
                print(f'frame {fi}: no plausible wheel segmentation (boxes alone are not used)')
                continue
            confidence, points, box = max(plausible, key=lambda c: c[0])
            mask = np.zeros((h, w), np.uint8)
            cv2.fillPoly(mask, [points.astype(np.int32)], 1)
            candidates.append((confidence, mask, box))
            print(f'frame {fi}: segmented wheel confidence {confidence:.3f}')
    finally:
        cap.release()

    polygon, used = wheel_polygon(candidates)
    margin = int(round(WRIST_MARGIN_FRAC * min(np.ptp(polygon, axis=0))))
    directory = os.path.dirname(out) or '.'
    os.makedirs(directory, exist_ok=True)
    preview = first_frame.copy()
    outlines = tolerance_outline(polygon, margin, (w, h))
    if outlines:
        cv2.polylines(preview, outlines, True, (255, 100, 0), 3)
    cv2.polylines(preview, [polygon], True, (255, 255, 0), 4)
    cv2.putText(preview, f'Cyan: wheel | Blue: wrist tolerance ({margin}px)',
                (20, 50), 0, 1.1, (255, 255, 255), 2)
    preview_path = os.path.join(directory, 'wheel_preview.jpg')
    if not cv2.imwrite(preview_path, cv2.resize(preview, None, fx=0.5, fy=0.5)):
        raise OSError(f'Cannot write wheel preview: {preview_path}')
    with open(out, 'w') as f:
        json.dump({'wheel': polygon.tolist(), 'frame_size': [w, h], 'source': 'auto_wheel',
                   'algorithm': 'mask_majority_v1', 'sample_count': len(used),
                   'wrist_margin_px': margin}, f)
    print(f'using {len(used)} reliable frames; saved {len(polygon)} points -> {out}')
    print(f'CHECK THE PREVIEW: {preview_path}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'out/wheel.json')
