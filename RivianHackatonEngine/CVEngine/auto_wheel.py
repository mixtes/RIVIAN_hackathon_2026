"""Find the steering wheel using temporal consensus of segmentation masks.
Usage (recorded video, samples spread across the whole clip):
    python3 auto_wheel.py my_video.mp4 [out/wheel.json]
Usage (live: only frames seen so far, locks once enough samples agree):
    python3 auto_wheel.py --live CAMERA_INDEX_OR_STREAM_OR_VIDEO [out/wheel.json]
Only pixels supported by a strict majority of reliable samples define the zone.
No bounding-box fallback or convex hull is applied to the wheel shape.
A separate, visible wrist tolerance is saved for pose-based hand checks.
Always inspect wheel_preview.jpg before trusting a calibration.
The camera and wheel are fixed per vehicle, so a verified wheel.json can be reused
instead of recalibrating every drive.
"""
import argparse, cv2, json, os, sys, time, numpy as np
from collections import deque

PROMPTS = ['steering wheel', 'car steering wheel']
N_FRAMES = 16
KEEP_FRAC = 0.4
CONF = 0.01                  # proposal threshold; final samples must also pass MIN_CONF
MIN_CONF = 0.15
MIN_SAMPLES = 3
AREA_MIN, AREA_MAX = 0.01, 0.10
MIN_IOU = 0.25
WRIST_MARGIN_FRAC = 0.15     # fraction of the wheel's shorter box dimension, not image width
LIVE_INTERVAL_S = 1.0        # spacing between live samples, so samples are not near-duplicates
LIVE_TIMEOUT_S = 60.0        # give up (wheel monitoring unavailable) if no consensus by then


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


def load_model():
    from ultralytics import YOLOE

    model = YOLOE('yoloe-11m-seg.pt')
    model.set_classes(PROMPTS, model.get_text_pe(PROMPTS))
    return model


def segment_wheel(model, frame):
    """Best plausible wheel mask in one frame as (confidence, points, box), or None."""
    h, w = frame.shape[:2]
    result = model.predict(frame, imgsz=960, conf=CONF, verbose=False)[0]
    plausible = []
    if result.masks is not None:
        for i, box in enumerate(result.boxes):
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            area = (x2-x1) * (y2-y1) / (w*h)
            if AREA_MIN <= area <= AREA_MAX and i < len(result.masks.xy):
                points = np.asarray(result.masks.xy[i], np.float32)
                if len(points) >= 3 and cv2.contourArea(points) > 0:
                    plausible.append((float(box.conf.item()), points, [x1, y1, x2, y2]))
    return max(plausible, key=lambda c: c[0]) if plausible else None


def rasterize(candidates, frame_size):
    w, h = frame_size
    out = []
    for confidence, points, box in candidates:
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [points.astype(np.int32)], 1)
        out.append((confidence, mask, box))
    return out


def calibration_record(polygon, used, frame_size, mode, **extra):
    margin = int(round(WRIST_MARGIN_FRAC * min(np.ptp(polygon, axis=0))))
    return dict(wheel=polygon.tolist(), frame_size=[int(v) for v in frame_size],
                source='auto_wheel', algorithm='mask_majority_v1', sample_count=len(used),
                wrist_margin_px=margin, mode=mode, **extra)


def save_calibration(record, frame, out):
    directory = os.path.dirname(out) or '.'
    os.makedirs(directory, exist_ok=True)
    polygon = np.array(record['wheel'], np.int32)
    margin = record['wrist_margin_px']
    preview = frame.copy()
    outlines = tolerance_outline(polygon, margin, tuple(record['frame_size']))
    if outlines:
        cv2.polylines(preview, outlines, True, (255, 100, 0), 3)
    cv2.polylines(preview, [polygon], True, (255, 255, 0), 4)
    cv2.putText(preview, f'Cyan: wheel | Blue: wrist tolerance ({margin}px)',
                (20, 50), 0, 1.1, (255, 255, 255), 2)
    preview_path = os.path.join(directory, 'wheel_preview.jpg')
    if not cv2.imwrite(preview_path, cv2.resize(preview, None, fx=0.5, fy=0.5)):
        raise OSError(f'Cannot write wheel preview: {preview_path}')
    with open(out, 'w') as f:
        json.dump(record, f)
    return preview_path


class LiveWheelCalibrator:
    """Causal wheel calibration: uses only frames received so far, then locks.

    Feed every frame with its capture timestamp. A frame is segmented only when
    `interval_s` has passed since the last sample, so samples are spread out.
    Once `samples` frames have been sampled, every new sample re-attempts the same
    consensus as recorded-video calibration, over a sliding window of the most
    recent `samples` attempts. Until LOCKED, wheel-based monitoring must be treated
    as unavailable; after `timeout_s` without consensus the state is FAILED.
    """
    CALIBRATING, LOCKED, FAILED = 'CALIBRATING', 'LOCKED', 'FAILED'

    def __init__(self, model, frame_size, interval_s=LIVE_INTERVAL_S, samples=N_FRAMES,
                 timeout_s=LIVE_TIMEOUT_S, segment=segment_wheel):
        if samples < MIN_SAMPLES or interval_s <= 0 or timeout_s <= 0:
            raise ValueError('Invalid live calibration settings.')
        self.model, self.frame_size, self.segment = model, tuple(frame_size), segment
        self.interval_s, self.samples, self.timeout_s = interval_s, samples, timeout_s
        self.reset()

    def reset(self):
        self.state, self.record, self.reason, self.preview_frame = self.CALIBRATING, None, '', None
        self.attempts = deque(maxlen=self.samples)    # (timestamp_s, candidate or None)
        self.start_s = self.last_sample_s = None

    def add_frame(self, frame, timestamp_s):
        if self.state != self.CALIBRATING:
            return self.state
        if self.last_sample_s is not None and timestamp_s < self.last_sample_s:
            self.reset()                              # seek or clock jump: earlier samples no longer apply
        if self.start_s is None:
            self.start_s = timestamp_s
        if self.last_sample_s is not None and timestamp_s - self.last_sample_s < self.interval_s:
            return self.state
        if tuple(frame.shape[1::-1]) != self.frame_size:
            raise ValueError(f'Frame size {frame.shape[1::-1]} differs from {self.frame_size}.')
        self.last_sample_s = timestamp_s
        self.attempts.append((timestamp_s, self.segment(self.model, frame)))
        if len(self.attempts) == self.samples:
            candidates = [c for _, c in self.attempts if c is not None]
            try:
                polygon, used = wheel_polygon(rasterize(candidates, self.frame_size))
            except ValueError as e:
                self.reason = str(e)
            else:
                self.state, self.preview_frame = self.LOCKED, frame.copy()
                self.record = calibration_record(
                    polygon, used, self.frame_size, 'live',
                    locked_after_s=round(timestamp_s - self.start_s, 3),
                    window_s=round(self.attempts[-1][0] - self.attempts[0][0], 3))
                return self.state
        if timestamp_s - self.start_s >= self.timeout_s:
            self.state = self.FAILED
            self.reason = (f'No wheel consensus within {self.timeout_s:.0f}s. '
                           f'{self.reason or "Not enough samples."}')
        return self.state


def main(video, out):
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
            if not ok:
                raise ValueError(f'Cannot decode sampled frame {fi} in {video}.')
            if first_frame is None:
                first_frame = frame.copy()
            candidate = segment_wheel(model, frame)
            if candidate is None:
                print(f'frame {fi}: no plausible wheel segmentation (boxes alone are not used)')
                continue
            candidates.append(candidate)
            print(f'frame {fi}: segmented wheel confidence {candidate[0]:.3f}')
    finally:
        cap.release()

    polygon, used = wheel_polygon(rasterize(candidates, (w, h)))
    record = calibration_record(polygon, used, (w, h), 'video')
    preview_path = save_calibration(record, first_frame, out)
    print(f'using {len(used)} reliable frames; saved {len(polygon)} points -> {out}')
    print(f'CHECK THE PREVIEW: {preview_path}')


def main_live(source, out):
    """Calibrate from a camera/stream as frames arrive. A video file is read
    sequentially, using its own timestamps, to simulate a live camera."""
    is_file = os.path.isfile(source)
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    try:
        if not cap.isOpened():
            raise ValueError(f'Cannot open live source: {source}')
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        calibrator = LiveWheelCalibrator(load_model(), (w, h))
        clock0 = time.monotonic()
        while calibrator.state == calibrator.CALIBRATING:
            ok, frame = cap.read()
            if not ok:
                break
            ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 if is_file else time.monotonic() - clock0
            previous = calibrator.last_sample_s
            calibrator.add_frame(frame, ts)
            if calibrator.last_sample_s != previous:
                last = calibrator.attempts[-1][1]
                print(f't={ts:6.2f}s sample {len(calibrator.attempts)}/{calibrator.samples}: '
                      + (f'confidence {last[0]:.3f}' if last else 'no plausible wheel'), flush=True)
    finally:
        cap.release()
    if calibrator.state != calibrator.LOCKED:
        reason = calibrator.reason or 'Stream ended before enough samples were collected.'
        sys.exit(f'Wheel calibration not locked ({calibrator.state}): {reason} '
                 'Wheel-based monitoring is unavailable; supply a verified manual wheel.json.')
    preview_path = save_calibration(calibrator.record, calibrator.preview_frame, out)
    print(f'LOCKED after {calibrator.record["locked_after_s"]}s using '
          f'{calibrator.record["sample_count"]} samples -> {out}')
    print(f'CHECK THE PREVIEW: {preview_path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', help='video file; with --live: camera index, stream URL, or video file')
    parser.add_argument('out', nargs='?', default='out/wheel.json')
    parser.add_argument('--live', action='store_true',
                        help='calibrate causally from frames as they arrive')
    args = parser.parse_args()
    (main_live if args.live else main)(args.source, args.out)
