"""Live phone-use monitor: per-frame phone evidence and confirmed PHONE_USE events.

Usage:
    python3 live_phone.py SOURCE [--show] [--record out.mp4] [--log frames.csv] [--events events.jsonl]
                                 [--engine] [--phone-model best.pt] [--dark-phone-model best.pt]
                                 [--stride N | --all-frames] [--max-seconds N]
SOURCE is a camera index (0), a stream URL, or a video file. A video file is played
at its own speed and, like a live camera, only the newest frame is processed;
frames that arrive while the models are busy are skipped. --stride N processes exactly
every Nth frame of a file instead (repeatable, for evaluation); --all-frames processes all.

Each processed frame gets POSITIVE / NEGATIVE / UNKNOWN phone-use evidence (see phone_events.py):
a phone at the ear needs a hand at the phone, a phone in the hand must move relative to the
cabin, and a missing driver is UNKNOWN. PHONE_USE events start after ~2 s of mostly positive
evidence and end after ~3 s without it. --engine feeds the events into SafetySystem
(AlertEngine + GameEngine from RivianHackatonEngine/main.py) and prints alerts and the score.
"""
import argparse, csv, json, os, sys, threading, time
import cv2, numpy as np

# Keep in sync with extract.py (same models, crop and driver side).
POSE_MODEL, DET_MODEL = 'yolo11m-pose.pt', 'yolo11m.pt'
CX0, CY0 = 760, 600                 # driver-area crop origin for the 1920x1536 cabin camera
REF_W, REF_H = 1920, 1536
DRIVER_ON_RIGHT = True
POSE_SIZE, DET_SIZE = 640, 640
PHONE_CONF = 0.15
KP_CONF = 0.35
NOSE, LEAR, REAR, LSHO, RSHO, LWR, RWR = 0, 3, 4, 5, 6, 9, 10
EAR_DIST, HAND_DIST = 0.8, 0.6      # fractions of shoulder width, as in rules.py
DEFAULT_SCALE = 350.0               # shoulder width (px) when shoulders are not visible
RECORD_FPS = 4                      # --record plays processed frames at about the live rate
DARK_BRIGHTNESS = 45                # mean gray level of the driver crop below which a frame counts as dark
DARK_CONF = 0.5                     # threshold for the fine-tuned dark-frame model (fewer false alarms)


def is_dark(crop_img):
    return float(cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY).mean()) < DARK_BRIGHTNESS


def crop_origin(frame):
    """Top-left of the driver-area crop, scaled from (CX0, CY0) on the reference camera to this frame size."""
    h, w = frame.shape[:2]
    return int(round(CX0 * w / REF_W)), int(round(CY0 * h / REF_H))


def phone_class_ids(model):
    """Class ids meaning 'phone': 67 ('cell phone') in COCO models, 0 in the fine-tuned model."""
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    ids = [k for k, v in names.items() if v in ('cell phone', 'phone')]
    if not ids:
        raise ValueError(f'Model has no phone class: {sorted(names.values())[:10]}...')
    return ids


def select_driver(boxes, keypoints, width):
    """Largest person whose box centre is on the driver's half of the image."""
    best, best_area = None, -1.0
    for b, k in zip(boxes, keypoints):
        if (((b[0] + b[2]) / 2 > width / 2) == DRIVER_ON_RIGHT):
            area = (b[2] - b[0]) * (b[3] - b[1])
            if area > best_area:
                best, best_area = k, area
    return best


def phone_flags(kp, phones):
    """Single-frame phone evidence for the driver.

    phones: list of (x1, y1, x2, y2, conf) in full-frame pixels.
    Returns dict(phone, conf, at_ear, in_hand).
    """
    result = dict(phone=bool(phones), conf=max((p[4] for p in phones), default=0.0),
                  at_ear=False, in_hand=False)
    if not phones or kp is None:
        return result
    ok = lambda i: kp[i, 2] >= KP_CONF
    sw = np.hypot(*(kp[LSHO, :2] - kp[RSHO, :2])) if ok(LSHO) and ok(RSHO) else DEFAULT_SCALE
    head = [kp[i, :2] for i in (LEAR, REAR, NOSE) if ok(i)]
    hands = [kp[i, :2] for i in (LWR, RWR) if ok(i)]
    centres = [np.array([(p[0] + p[2]) / 2, (p[1] + p[3]) / 2]) for p in phones]
    result['at_ear'] = any(np.hypot(*(c - h)) < EAR_DIST * sw for c in centres for h in head)
    result['in_hand'] = (not result['at_ear'] and
                         any(np.hypot(*(c - w)) < HAND_DIST * sw for c in centres for w in hands))
    return result


class LatestFrame:
    """Reader thread keeping only the newest frame (a bounded queue of one)."""

    def __init__(self, source, realtime, stride=1):
        self.is_file = os.path.isfile(source)
        self.stride = max(1, stride) if self.is_file else 1
        self.cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
        if not self.cap.isOpened():
            raise ValueError(f'Cannot open source: {source}')
        self.realtime = realtime or not self.is_file
        self.cond = threading.Condition(threading.Lock())
        self.item, self.read_count, self.ended = None, 0, False
        self.consumed = threading.Event()
        self.consumed.set()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.clock0 = time.monotonic()
        self.thread.start()
        return self

    def _run(self):
        index = 0
        while not self.ended:
            if not self.realtime:
                self.consumed.wait()               # --all-frames / --stride: hand over frames in order
            if self.stride > 1 and index % self.stride:
                if not self.cap.grab():
                    break
                index += 1
                self.read_count = index
                continue
            ok, frame = self.cap.read()
            if not ok:
                break
            if self.is_file:
                ts = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if self.realtime:                  # play the file at its own speed
                    delay = ts - (time.monotonic() - self.clock0)
                    if delay > 0:
                        time.sleep(delay)
            else:
                ts = time.monotonic() - self.clock0
            with self.cond:
                self.item = (index, ts, frame)
                self.read_count = index + 1
                self.consumed.clear()
                self.cond.notify()
            index += 1
        with self.cond:
            self.ended = True
            self.cond.notify()

    def get(self, last_index):
        """Wait for a frame newer than last_index; None when the source has ended."""
        with self.cond:
            while not self.ended and (self.item is None or self.item[0] == last_index):
                self.cond.wait(0.5)
            item = self.item if self.item is not None and self.item[0] != last_index else None
        self.consumed.set()
        return item

    def stop(self):
        self.ended = True
        self.consumed.set()
        self.thread.join(timeout=2)
        self.cap.release()


class PhoneMonitor:
    """PHONE_USE for one camera, frame by frame, for a pipeline that already has the driver keypoints
    (e.g. live.py). update(t, frame, kp) -> 1/0: the confirmed PHONE_USE state (phone_events.EventTracker),
    already debounced (~1 s to switch on, ~3 s to switch off), so it needs no extra flicker filter.
    The dark-frame model is optional (it is git-ignored); without it dark phones are found less often."""

    DEFAULT_DARK_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'phone_dataset', 'runs', 'phone_ft', 'weights', 'best.pt')

    def __init__(self, phone_model=DET_MODEL, dark_phone_model=DEFAULT_DARK_MODEL, hands=True, device=None):
        from ultralytics import YOLO
        from phone_events import CabinMotion, EventTracker, PhoneEvidence
        self.det, self.device = YOLO(phone_model), device
        self.ids = phone_class_ids(self.det)
        self.det_dark = YOLO(dark_phone_model) if dark_phone_model and os.path.isfile(dark_phone_model) else None
        self.dark_ids = phone_class_ids(self.det_dark) if self.det_dark else None
        self.finder = None
        if hands:
            try:
                from hands import HandFinder
                self.finder = HandFinder()
            except ImportError:
                pass
        self.evidence, self.tracker, self.cabin = PhoneEvidence(), EventTracker(), CabinMotion()
        self.last = None                 # (evidence, phones, used_dark_model) of the latest frame

    def update(self, t, frame, kp):
        x0, y0 = crop_origin(frame)
        crop = frame[y0:, x0:]
        dark = self.det_dark is not None and is_dark(crop)
        model, ids, conf = (self.det_dark, self.dark_ids, DARK_CONF) if dark else (self.det, self.ids, PHONE_CONF)
        d = model(crop, imgsz=DET_SIZE, conf=conf, classes=ids, verbose=False, device=self.device)[0]
        phones = [(b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0, float(c))
                  for b, c in zip(d.boxes.xyxy.cpu().numpy(), d.boxes.conf.cpu().numpy())][:3]
        hands = (lambda: self.finder.find(frame, kp)) if self.finder is not None and kp is not None else None
        ev = self.evidence.update(t, kp, phones, self.cabin.shift(frame), hands)
        self.tracker.update(ev)
        self.last = (ev, phones, dark)
        return int(self.tracker.active is not None)

    @property
    def active(self):
        return self.tracker.active is not None


def record_codec(path):
    """VP8 for .webm (plays in VS Code and browsers), mp4v otherwise (converted by to_h264 afterwards)."""
    return 'VP80' if path.lower().endswith('.webm') else 'mp4v'


def to_h264(path):
    """Re-encode an mp4v .mp4 to H.264 so VS Code and browsers can play it. OpenCV's own H.264 writer
    needs a matching OpenH264 DLL, so this uses the ffmpeg from imageio-ffmpeg; keeps mp4v if unavailable."""
    if not path.lower().endswith('.mp4') or not os.path.isfile(path):
        return False
    try:
        import imageio_ffmpeg
        import subprocess
        tmp = path[:-4] + '.h264.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error', '-i', path, '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', tmp], check=True)
        os.replace(tmp, path)
        return True
    except Exception as e:                      # noqa: BLE001  (the mp4v file is still usable in VLC)
        print(f'recording kept as mp4v (not playable in VS Code): {e}', flush=True)
        return False


def load_safety_system():
    """SafetySystem from RivianHackatonEngine/main.py (the parent folder of CVEngine)."""
    engine_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)
    from main import SafetySystem
    return SafetySystem(on_clear=lambda c: print(f'  [{c.timestamp:6.2f}s] ENGINE CLEAR {c.action} '
                                                 f'(was {c.last_level.name})', flush=True))


def main():
    from phone_events import CabinMotion, PhoneEvidence, PhoneUseTracker, POSITIVE, UNKNOWN, ACTION
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source')
    parser.add_argument('--show', action='store_true', help='open a window with the detections')
    parser.add_argument('--all-frames', action='store_true',
                        help='video files only: process every frame instead of live skipping')
    parser.add_argument('--stride', type=int, default=1,
                        help='video files only: process exactly every Nth frame (repeatable evaluation)')
    parser.add_argument('--max-seconds', type=float, default=None, help='stop after this source time')
    parser.add_argument('--log', default=None, help='CSV file with one row per processed frame')
    parser.add_argument('--events', default=None, help='JSONL file with PHONE_USE start/end updates')
    parser.add_argument('--engine', action='store_true',
                        help='feed PHONE_USE events into SafetySystem and print alerts and the drive score')
    parser.add_argument('--quiet', action='store_true', help='print only events, alerts and the summary')
    parser.add_argument('--phone-model', default=DET_MODEL,
                        help='phone detector weights, e.g. a fine-tuned best.pt from phone_data.py train')
    parser.add_argument('--dark-phone-model', default=None,
                        help='detector used instead when the driver area is dark (e.g. the fine-tuned best.pt)')
    parser.add_argument('--dark-conf', type=float, default=DARK_CONF,
                        help='confidence threshold for the dark-frame detector')
    parser.add_argument('--record', default=None,
                        help='save each processed frame with its detections (.mp4 is converted to H.264, '
                             '.webm is VP8; both play in VS Code)')
    parser.add_argument('--no-hands', action='store_true',
                        help='skip hand landmarks; a still phone while looking down is then never confirmed')
    args = parser.parse_args()

    from ultralytics import YOLO
    pose, det = YOLO(POSE_MODEL), YOLO(args.phone_model)
    finder = None
    if not args.no_hands:
        try:
            from hands import HandFinder
            finder = HandFinder()
        except ImportError as e:
            print(f'hand landmarks unavailable ({e}); install requirements-hands.txt with --no-deps', flush=True)
    phone_ids = phone_class_ids(det)
    det_dark = YOLO(args.dark_phone_model) if args.dark_phone_model else None
    dark_ids = phone_class_ids(det_dark) if det_dark else None
    system = load_safety_system() if args.engine else None
    evidence, tracker, cabin = PhoneEvidence(), PhoneUseTracker(), CabinMotion()
    fixed = args.all_frames or args.stride > 1
    reader = LatestFrame(args.source, realtime=not fixed, stride=1 if args.all_frames else args.stride).start()
    recorder = cv2.VideoWriter() if args.record else None
    log = open(args.log, 'w', newline='') if args.log else None
    writer = csv.writer(log) if log else None
    events_out = open(args.events, 'w') if args.events else None
    if writer:
        writer.writerow(['frame', 'time_s', 'latency_s', 'phone', 'conf', 'evidence', 'reason',
                         'event_active', 'driver_found', 'dark_model'])
    stats = dict(processed=0, phone=0, positive=0, unknown=0, dark=0)
    last, started, latencies, last_ts, first_ts = None, time.monotonic(), [], None, None
    if not args.quiet:
        print('time     frame  latency  phone  conf  evidence  reason            (D = dark-frame model)')

    def emit(kind, event, ts):
        record = dict(update=kind, event_id=event.event_id, action=event.action,
                      onset_s=round(event.onset_s, 3), confirmed_at_s=round(event.confirmed_at_s, 3),
                      last_supported_s=round(event.last_supported_s, 3),
                      end_s=None if event.end_s is None else round(event.end_s, 3),
                      end_reason=event.end_reason, positive_frames=event.positive_frames)
        print(f'*** {event.action} {kind.upper()}  #{event.event_id}  at {ts:.2f}s  '
              + (f'(onset {event.onset_s:.2f}s)' if kind == 'start' else
                 f'(supported until {event.last_supported_s:.2f}s, {event.end_reason})'), flush=True)
        if events_out:
            events_out.write(json.dumps(record) + '\n')
            events_out.flush()
        if system is not None:
            alert = system.on_event(ACTION, kind == 'start', ts)
            if alert:
                print(f'  [{ts:6.2f}s] ENGINE ALERT {alert.level.name}: {alert.message}', flush=True)

    try:
        while True:
            item = reader.get(last)
            if item is None:
                break
            index, ts, frame = item
            last = index
            if args.max_seconds is not None and ts > args.max_seconds:
                break
            if first_ts is None:
                first_ts = ts
                if system is not None:
                    system.start_drive(ts)
            last_ts = ts
            t0 = time.monotonic()
            h, w = frame.shape[:2]
            r = pose(frame, imgsz=POSE_SIZE, conf=0.25, verbose=False)[0]
            kp = select_driver(r.boxes.xyxy.cpu().numpy(), r.keypoints.data.cpu().numpy(), w)
            x0, y0 = crop_origin(frame)
            crop_img = frame[y0:, x0:]
            use_dark = det_dark is not None and is_dark(crop_img)
            model, ids, conf = (det_dark, dark_ids, args.dark_conf) if use_dark else (det, phone_ids, PHONE_CONF)
            d = model(crop_img, imgsz=DET_SIZE, conf=conf, classes=ids, verbose=False)[0]
            phones = [(b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0, float(c))
                      for b, c in zip(d.boxes.xyxy.cpu().numpy(), d.boxes.conf.cpu().numpy())][:3]
            hands = (lambda: finder.find(frame, kp)) if finder is not None else None
            ev = evidence.update(ts, kp, phones, cabin.shift(frame), hands)
            updates = tracker.update(ev)
            latency = time.monotonic() - t0
            latencies.append(latency)
            stats['processed'] += 1
            stats['dark'] += use_dark
            stats['phone'] += bool(phones)
            stats['positive'] += ev.state == POSITIVE
            stats['unknown'] += ev.state == UNKNOWN
            best_conf = max((p[4] for p in phones), default=0.0)
            if not args.quiet:
                print(f'{ts:6.2f}s  {index:5d}  {latency:5.2f}s  {"YES" if phones else "no ":4s}  {best_conf:4.2f}  '
                      f'{ev.state:8s}  {ev.reason}{" D" if use_dark else ""}', flush=True)
            for kind, event in updates:
                emit(kind, event, ts)
            if system is not None:
                for alert in system.tick_all(ts):
                    print(f'  [{ts:6.2f}s] ENGINE ALERT {alert.level.name}: {alert.message}', flush=True)
            if writer:
                writer.writerow([index, round(ts, 3), round(latency, 3), int(bool(phones)), round(best_conf, 3),
                                 ev.state, ev.reason, int(tracker.active is not None), int(kp is not None),
                                 int(use_dark)])
            if args.show or recorder is not None:
                view = frame.copy()
                for p in phones:
                    color = (0, 0, 255) if ev.state == POSITIVE and ev.phone is not None and p[:4] == ev.phone[:4] \
                        else (255, 0, 255)
                    cv2.rectangle(view, (int(p[0]), int(p[1])), (int(p[2]), int(p[3])), color, 4)
                    cv2.putText(view, f'phone {p[4]:.2f}', (int(p[0]), int(p[1]) - 10), 0, 1.2, color, 3)
                if kp is not None:
                    for j in (NOSE, LEAR, REAR, LWR, RWR):
                        if kp[j, 2] >= KP_CONF:
                            cv2.circle(view, tuple(kp[j, :2].astype(int)), 12, (0, 255, 0), -1)
                active = tracker.active is not None
                cv2.rectangle(view, (0, 0), (w, 150), (0, 0, 160) if active else (0, 0, 0), -1)
                cv2.putText(view, f't={ts:.2f}s  evidence: {ev.state}  ({ev.reason})'
                                  f'{"  [dark model]" if use_dark else ""}', (20, 55), 0, 1.3, (255, 255, 255), 3)
                cv2.putText(view, 'PHONE_USE ACTIVE' if active else 'no phone use', (20, 125), 0, 1.8,
                            (255, 255, 255), 4)
                view = cv2.resize(view, (960, int(960 * h / w)))
                if recorder is not None:
                    if not recorder.isOpened():
                        recorder.open(args.record, cv2.VideoWriter_fourcc(*record_codec(args.record)), RECORD_FPS,
                                      view.shape[1::-1])
                        if not recorder.isOpened():
                            raise OSError(f'Cannot write recording: {args.record}')
                    recorder.write(view)
                if args.show:
                    cv2.imshow('live phone detection (q to quit)', view)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
        if last_ts is not None:
            for kind, event in tracker.finish(last_ts):
                emit(kind, event, last_ts)
    finally:
        reader.stop()
        for f in (log, events_out):
            if f:
                f.close()
        if recorder is not None:
            recorder.release()
            to_h264(args.record)
        if args.show:
            cv2.destroyAllWindows()

    elapsed = time.monotonic() - started
    n = stats['processed']
    print('\n--- summary ---')
    print(f'frames read from source: {reader.read_count}, processed: {n}, '
          f'not processed: {reader.read_count - n}')
    if n:
        print(f'processing rate: {n / elapsed:.1f} frames/s, median latency {np.median(latencies):.2f}s')
        print(f'phone box in {stats["phone"]}/{n} frames; phone-use evidence POSITIVE in {stats["positive"]}, '
              f'UNKNOWN in {stats["unknown"]}')
        if det_dark is not None:
            print(f'dark-frame model used on {stats["dark"]}/{n} frames')
    events = tracker.events
    print(f'PHONE_USE events: {len(events)}' + ''.join(
        f'\n  #{e.event_id}: {e.onset_s:.2f}s - {e.end_s:.2f}s (confirmed at {e.confirmed_at_s:.2f}s, '
        f'{e.end_reason})' for e in events))
    if system is not None and last_ts is not None:
        print(system.end_drive(last_ts))
    for name, path in (('per-frame log', args.log), ('events', args.events), ('recording', args.record)):
        if path:
            print(f'{name}: {path}')


if __name__ == '__main__':
    main()
