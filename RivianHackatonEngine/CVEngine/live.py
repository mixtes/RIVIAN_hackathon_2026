"""LIVE hands-on-wheel monitor. Frames are consumed one at a time; nothing after the current frame is ever used.
Usage: python3 live.py SOURCE [--method hybrid|pose|mediapipe] [--stride N] [--show] [--save out.mp4] [--events events.jsonl] [--frames frames.csv] [--output simple.csv] [--flags flags.csv] [--stream] [--play] [--wait] [--head face|pose] [--realtime]
  SOURCE: a camera index (0, 1, ...) or a video file that is replayed as if it were a live stream.
  --stride N  run the models on every Nth frame only (default 1). Timing always uses source time.
  --realtime  play a video file at its own speed, like a camera (a camera is always live). Live sources keep only
              the newest frame: frames that arrive while the models are busy are dropped, so output never lags.
  --method    how a hand is judged near / away / unknown (default hybrid):
                hybrid     hands_hybrid.py     MediaPipe landmarks, pose proxy fills the gaps (recommended)
                pose       hands_pose.py       pose keypoints only (fastest, least accurate)
                mediapipe  hands_mediapipe.py  MediaPipe landmarks only (many 'unknown' when a hand grips the rim)
Stages: (1) wheel calibration from the first WARMUP frames fed (WheelCalibrator, prior fallback labelled);
(2) per frame: driver pose -> hand method -> near / away / unknown per hand -> causal state machine.
Events are printed the moment they are confirmed or ended: HANDS_OFF_THE_WHEEL (public name).
  --head        how the head is measured (default face): face = MediaPipe face angles in degrees, pose keypoints
                as the fallback where no face is found; pose = pose keypoints only (no face model)
  --flags FILE  the byte sent to the AlertEngine/GameEngine per analyzed frame (cv_flags.py): 0x20 hands off the
                wheel, 0x40 head turned away or down/up (eyes_off_road.py), lightly smoothed; 0x10 phone use (live_phone.PhoneMonitor,
                confirmed: ~1 s to switch on, ~3 s to switch off). CSV: frame,time_s,flags,hands_off,eyes_off_road,phone
  --no-hands    skip hands-off-wheel detection and wheel calibration (bit 0x20 stays 0; faster)
  --no-phone    skip phone detection; --dark-phone-model FILE sets the fine-tuned dark-frame phone model
  --pose-model / --pose-size, --phone-model / --phone-size   try other (e.g. smaller, faster) YOLO models;
                the defaults are yolo11m-pose.pt and yolo11m.pt at 640 px
  --no-yawn     skip yawn detection (bit 0x08: mouth opened wide, then covered by a hand; yawn.py; needs --head face)
  --play        show the plain video (every frame, no overlays) in a window at its real speed; q closes it and stops.
                A player thread reads and shows the source; the analysis takes the newest frame (at least --stride
                frames after the previous one) and skips frames it cannot keep up with, so the video never slows down
  --wait        load the models and calibrate the wheel on the first frames (a video is rewound afterwards), print
                READY to stderr, then wait for one line on stdin before playing (used by the bridge: the video starts
                when the car app starts a drive)
"""
import argparse, json, sys, threading, time
import cv2, numpy as np
import auto_wheel, rules
import hands_pose
import cv_flags, eyes_off_road, eyes_closed, occupancy, slump, yawn

CASE = 'HANDS_OFF_THE_WHEEL'
HOLD_S = 1.0        # both hands observed away for this long -> confirmed
CANDIDATE_RESET_S = 0.3   # a hand seen near the wheel this long cancels an unconfirmed candidate
CLEAR_S = 0.5       # a hand seen near the wheel this long ends a confirmed event
LOSS_S = 2.0        # no usable observation for this long -> observation lost (event ends, state resets)
rules_sk = [(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(0,1),(0,2),(1,3),(2,4)]
POSE_MODEL, POSE_SIZE, DRIVER_ON_RIGHT = 'yolo11m-pose.pt', 640, True
PLAY_WINDOW, PLAY_MAX_WIDTH = 'DriveScore - driver camera', 1280    # --play


def pick_device():
    try:
        import torch
        if torch.backends.mps.is_available():
            return 'mps'
        if torch.cuda.is_available():
            return 0
    except Exception:
        pass
    return 'cpu'


def driver_keypoints(result, width):
    """Largest person on the driver's side of the frame (same convention as extract.py), or None."""
    best, area = None, 0.0
    for box, kp in zip(result.boxes.xyxy.cpu().numpy(), result.keypoints.data.cpu().numpy()):
        if (((box[0] + box[2]) / 2) > width / 2) != DRIVER_ON_RIGHT:
            continue
        a = (box[2] - box[0]) * (box[3] - box[1])
        if a > area:
            best, area = kp, a
    return best


def hands_mediapipe_connections():
    import hands_mediapipe
    return hands_mediapipe.CONNECTIONS


def make_hands(method, wheel, margin):
    """Build the hand method; MediaPipe is imported only when the method needs it."""
    if method == 'pose':
        return hands_pose.PoseHands(wheel, margin)
    if method == 'mediapipe':
        import hands_mediapipe
        return hands_mediapipe.MediaPipeHands(wheel, margin)
    import hands_hybrid
    return hands_hybrid.HybridHands(wheel, margin)


class NoHands:
    """--no-hands: stands in for a hand method without running anything. Every hand is 'unknown', so the
    hands-off bit (0x20) stays 0 and HandsOffMachine never confirms; no wheel calibration is needed."""

    def update(self, frame, kp):
        return ['unknown', 'unknown'], dict(points=[None, None], landmarks=[None, None], sources=['off', 'off'])


def classify(states):
    if 'near' in states:
        return 'near'               # one reliably near hand is negative evidence for hands-off
    if states.count('away') == 2:
        return 'off'                # both hands observed and away
    return 'unknown'               # a hidden hand is not evidence of anything


def hands_off_flag(label):
    """Simple per-frame output: (hands_off, unknown).
    hands_off = 1 only when both hands were observed and neither is on the wheel; 0 when at least one hand is on it.
    'unknown' (a hand hidden and no hand on the wheel) gives hands_off = 0 with unknown = 1: a hidden hand is not
    evidence of 'no hands on the wheel'."""
    return (1 if label == 'off' else 0), (1 if label == 'unknown' else 0)


class HandsOffMachine:
    """Causal state machine. update(t, label) -> list of events, using only samples up to t."""

    def __init__(self):
        self.reset()
        self.counter = 0

    def reset(self):
        self.cand_start = self.near_since = None
        self.last_obs = None
        self.confirmed = False
        self.event_id = None
        self.supported_s = 0.0
        self.prev_t = self.prev_label = None

    def update(self, t, label):
        events = []
        if self.last_obs is not None and t - self.last_obs > LOSS_S:        # observation lost
            if self.confirmed:
                events.append(self._end(self.last_obs, 'observation_lost'))
            self.reset()
        if label != 'unknown':
            self.last_obs = t
        if label == 'off':
            self.near_since = None
            if self.cand_start is None:
                self.cand_start = t
            if self.prev_label == 'off' and self.prev_t is not None:
                self.supported_s += t - self.prev_t        # unknown gaps are never counted as supported
            if not self.confirmed and t - self.cand_start >= HOLD_S:
                self.confirmed = True
                self.counter += 1
                self.event_id = f'{CASE}-{self.counter:04d}'
                events.append(dict(type='confirmed', case=CASE, event_id=self.event_id,
                                   onset_t=round(self.cand_start, 3), confirmed_t=round(t, 3)))
        elif label == 'near':
            if self.near_since is None:
                self.near_since = t
            held = t - self.near_since
            if self.confirmed and held >= CLEAR_S:
                events.append(self._end(self.near_since, 'cleared'))
                self.reset()
            elif not self.confirmed and held >= CANDIDATE_RESET_S:
                self.cand_start, self.supported_s = None, 0.0
        self.prev_t, self.prev_label = t, label
        return events

    def _end(self, t, reason):
        return dict(type='ended', case=CASE, event_id=self.event_id, end_t=round(t, 3),
                    reason=reason, supported_s=round(self.supported_s, 3))

    def finish(self, t):
        if self.confirmed:
            return [self._end(t, 'stream_end')]
        return []


class Player(threading.Thread):
    """--play: reads the source and shows it at its own speed, independent of the analysis.

    The analysis calls next() and gets the newest frame, so a slow CPU drops analysis frames instead of
    slowing the video down. A video file is paced at its fps; a camera delivers frames in real time.
    """

    def __init__(self, cap, fps, is_cam, started):
        super().__init__(daemon=True)
        self.cap, self.fps, self.is_cam, self.started = cap, fps, is_cam, started
        self.cond = threading.Condition()
        self.latest = None               # (index, t, frame) of the newest frame read
        self.done = False
        self.read = self.drawn = self.late = 0
        self.max_draw_gap = 0.0          # longest time the window showed the same frame (s)

    def run(self):
        n = 0
        last_draw = time.monotonic()
        cv2.namedWindow(PLAY_WINDOW, cv2.WINDOW_AUTOSIZE)
        if hasattr(cv2, 'WND_PROP_TOPMOST'):  # keep it above the emulator / terminals (Windows)
            cv2.setWindowProperty(PLAY_WINDOW, cv2.WND_PROP_TOPMOST, 1)
        try:
            while not self.done:
                ok, frame = self.cap.read()
                if not ok:
                    break
                t = (time.monotonic() - self.started) if self.is_cam else n / self.fps
                late = False
                if not self.is_cam:
                    delay = self.started + t - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    late = delay < -0.25         # behind schedule: skip drawing to catch up
                with self.cond:
                    self.latest = (n, t, frame)
                    self.cond.notify_all()
                n += 1
                self.read += 1
                if late:
                    self.late += 1
                else:
                    now = time.monotonic()
                    self.max_draw_gap = max(self.max_draw_gap, now - last_draw)
                    last_draw = now
                    self.drawn += 1
                    scale = min(1.0, PLAY_MAX_WIDTH / frame.shape[1])
                    cv2.imshow(PLAY_WINDOW, cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
        finally:
            with self.cond:
                self.done = True
                self.cond.notify_all()
            cv2.destroyAllWindows()

    def next(self, after, min_gap):
        """The newest frame whose index is >= after + min_gap, or None once the source has ended."""
        with self.cond:
            while not self.done and (self.latest is None or self.latest[0] < after + min_gap):
                self.cond.wait(0.5)
            if self.done:
                return None
            return self.latest

    def stop(self):
        with self.cond:
            self.done = True
            self.cond.notify_all()


def precalibrate(cap, cal, stride, is_cam):
    """--wait: lock the wheel on the first frames before the drive starts, then rewind a video."""
    n = 0
    while cal.status == 'collecting':
        ok, frame = cap.read()
        if not ok:
            break
        if n % stride == 0:
            cal.add_frame(frame)
        n += 1
    if not is_cam:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)


def open_source(source):
    cam = source.isdigit()
    cap = cv2.VideoCapture(int(source) if cam else source)
    if not cap.isOpened():
        sys.exit(f'Cannot open source: {source}')
    return cap, cam


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source')
    ap.add_argument('--stride', type=int, default=1)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--save')
    ap.add_argument('--events')
    ap.add_argument('--frames', help='CSV with the per-frame hand data (state, wrist and hand points)')
    ap.add_argument('--realtime', action='store_true')
    ap.add_argument('--output', help='simple per-frame CSV: frame,time_s,hands_off,unknown (1 = no hands on the wheel)')
    ap.add_argument('--flags', help='CSV of the interface byte: frame,time_s,flags,hands_off,eyes_off_road')
    ap.add_argument('--stream', action='store_true',
                    help='print one record per analyzed frame to stdout: "timestamp,flags,occupants" (flags = the '
                         'interface byte 0..255, occupants = people in the car); every other message goes to stderr')
    ap.add_argument('--eyes-bit', action='store_true', help='kept for compatibility: the 0x80 bit is now on by default')
    ap.add_argument('--no-sleep-bit', action='store_true',
                    help='do not put the asleep / slumped state into bit 0x80 (it is only logged and drawn)')
    ap.add_argument('--head', choices=('face', 'pose'), default='face')
    ap.add_argument('--method', choices=('hybrid', 'pose', 'mediapipe'), default='hybrid')
    ap.add_argument('--no-phone', action='store_true', help='do not run phone detection (bit 0x10 stays 0)')
    ap.add_argument('--no-yawn', action='store_true', help='do not run yawn detection (bit 0x08 stays 0)')
    ap.add_argument('--no-hands', action='store_true',
                    help='do not run hands-off-wheel detection or wheel calibration (bit 0x20 stays 0); faster, and '
                         'other detections start on the first frame')
    ap.add_argument('--pose-model', default=POSE_MODEL,
                    help=f'pose model weights (default {POSE_MODEL}; e.g. yolo11s-pose.pt / yolo11n-pose.pt are faster)')
    ap.add_argument('--pose-size', type=int, default=POSE_SIZE, help=f'pose input size in px (default {POSE_SIZE})')
    ap.add_argument('--phone-model', default=None,
                    help='phone detector weights for normal frames (default yolo11m.pt; e.g. yolo11s.pt is faster)')
    ap.add_argument('--phone-size', type=int, default=None, help='phone detector input size in px (default 640)')
    ap.add_argument('--dark-phone-model', default=None,
                    help='fine-tuned phone model for dark frames (default: phone_dataset/runs/phone_ft/weights/'
                         'best.pt if present)')
    ap.add_argument('--play', action='store_true', help='plain video window at real speed, no overlays')
    ap.add_argument('--wait', action='store_true',
                    help='load models and calibrate, print READY to stderr, wait for a stdin line before playing')
    args = ap.parse_args()
    stream_out = sys.stdout
    if args.stream:
        sys.stdout = sys.stderr          # diagnostics must not mix into the record stream

    from ultralytics import YOLO
    device = pick_device()
    auto_wheel.DEVICE = device
    pose = YOLO(args.pose_model)
    print(f'device={device}; loading models and warming up...', flush=True)
    pose.predict(np.zeros((640, 640, 3), np.uint8), imgsz=args.pose_size, verbose=False, device=device)
    cal = None
    if not args.no_hands:
        cal = auto_wheel.WheelCalibrator(prior_fallback=True)
        cal.model = auto_wheel.load_model()
        cal.model.predict(np.zeros((640, 640, 3), np.uint8), imgsz=960, verbose=False, device=device)
    else:
        print('hands-off-wheel detection off (--no-hands): no wheel calibration, bit 0x20 stays 0', flush=True)

    faces = None
    if args.head == 'face':
        import head_face
        faces = head_face.FaceHead()
    phone = None
    if not args.no_phone:
        from live_phone import PhoneMonitor, DET_MODEL, DET_SIZE
        phone = PhoneMonitor(phone_model=args.phone_model or DET_MODEL,
                             dark_phone_model=args.dark_phone_model or PhoneMonitor.DEFAULT_DARK_MODEL,
                             device=device, det_size=args.phone_size or DET_SIZE)
        print(f'phone detection on ({args.phone_model or DET_MODEL} @ {args.phone_size or DET_SIZE}px; '
              f'dark-frame model: {"yes" if phone.det_dark else "no"})', flush=True)
    print(f'pose model {args.pose_model} @ {args.pose_size}px', flush=True)
    yawner = hand_finder = None
    if not args.no_yawn and faces is not None:
        yawner = yawn.YawnDetector()
        if phone is not None and phone.finder is not None:
            hand_finder = phone.finder                       # share the phone's MediaPipe hand finder
        else:
            try:
                from hands import HandFinder
                hand_finder = HandFinder()
            except ImportError:
                hand_finder = None                           # without hands only uncovered yawns are found
    elif not args.no_yawn:
        print('yawn detection off: it needs --head face', flush=True)
    cap, is_cam = open_source(args.source)
    if args.wait:
        if cal is not None:
            cal_started = time.monotonic()
            precalibrate(cap, cal, args.stride, is_cam)
            if cal.status == 'failed':
                sys.exit(f'Wheel calibration failed: {cal.error}')
            if cal.status == 'locked':
                print(f'wheel LOCKED before the drive: source={cal.info["source"]} method={args.method} '
                      f'(calibration took {time.monotonic() - cal_started:.1f}s)', flush=True)
        print('READY', file=sys.stderr, flush=True)
        if not sys.stdin.readline():         # EOF: whoever started us went away
            return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    writer = None
    log = open(args.events, 'w') if args.events else None
    fcsv = open(args.frames, 'w') if args.frames else None
    ocsv = open(args.output, 'w') if args.output else None
    bcsv = open(args.flags, 'w') if args.flags else None
    if bcsv:
        bcsv.write('frame,time_s,flags,hands_off,eyes_off_road,occupants,phone,yawn\n')
    head = eyes_off_road.HeadPoseDetector()
    eyes = eyes_closed.EyeClosureDetector()
    slumper = slump.SlumpDetector()
    people = occupancy.OccupancyCounter()
    hands_filter, head_filter, sleep_filter = cv_flags.FlickerFilter(), cv_flags.FlickerFilter(), cv_flags.FlickerFilter()
    if ocsv:
        ocsv.write('frame,time_s,hands_off,unknown\n')
    if fcsv:
        fcsv.write('frame,time_s,label,left_hand,right_hand,left_wrist_x,left_wrist_y,left_hand_x,left_hand_y,'
                   'right_wrist_x,right_wrist_y,right_hand_x,right_hand_y,event_active,left_source,right_source,head_yaw,head_delta,head_pitch,head_pitch_delta,head_state,head_direction,head_reason,'
                   'baseline_source,head_source,blink,gaze_down,eye_state,eye_score,eye_ref,eye_closed_s,eye_onset_s,ear,perclos,microsleeps,drowsy,slump_state,slump_ratio,slumped_s,'
                   'occupants,occ_driver,occ_front,occ_rear,occ_raw\n')
    machine, started = HandsOffMachine(), time.monotonic()
    frame_no = proc = 0
    wheel = margin = None
    hands = NoHands() if args.no_hands else None
    if cal is not None and cal.status == 'locked':     # calibrated during --wait
        wheel, margin = cal.polygon, cal.margin
        hands = make_hands(args.method, wheel, margin)
    player = Player(cap, fps, is_cam, started) if args.play else None
    if player:
        player.start()
    last_idx = -args.stride
    lat = []
    t = 0.0
    reader, last_index = None, None
    if not player and (args.realtime or is_cam):
        # Live without --play: a reader thread keeps only the newest frame, so a slow frame never builds a
        # backlog (otherwise the output falls further and further behind real time).
        from live_phone import LatestFrame
        cap.release()
        reader = LatestFrame(args.source, realtime=True, stride=args.stride).start()
    try:
        while True:
            if player:
                newest = player.next(last_idx, args.stride)
                if newest is None:
                    break
                last_idx, t, frame = newest
                frame_no = last_idx + 1
            elif reader is not None:
                item = reader.get(last_index)
                if item is None:
                    break
                last_index, t, frame = item
                frame_no = last_index + 1
            else:                                # offline file: every --stride-th frame, as fast as possible
                ok, frame = cap.read()
                if not ok:
                    break
                t = frame_no / fps
                frame_no += 1
                if (frame_no - 1) % args.stride:
                    continue
            t0 = time.monotonic()
            if cal is not None and cal.status == 'collecting':
                if cal.add_frame(frame) == 'collecting':
                    continue
                if cal.status == 'locked':
                    wheel, margin = cal.polygon, cal.margin
                    hands = make_hands(args.method, wheel, margin)
                    print(f'[t={t:6.2f}s] wheel LOCKED source={cal.info["source"]} method={args.method} '
                          f'(calibration took {time.monotonic() - started:.1f}s of wall time)', flush=True)
                continue
            if cal is not None and cal.status == 'failed':
                sys.exit(f'Wheel calibration failed: {cal.error}')
            result = pose.predict(frame, imgsz=args.pose_size, conf=0.25, verbose=False, device=device)[0]
            kp = driver_keypoints(result, frame.shape[1]) if result.keypoints is not None else None
            if result.keypoints is not None:
                occ = people.update(t, result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                    result.keypoints.data.cpu().numpy(), (frame.shape[1], frame.shape[0]))
            else:
                occ = people.update(t, [], [], [], (frame.shape[1], frame.shape[0]))
            states, dbg = hands.update(frame, kp)
            points = dbg['points']
            label = classify(states)
            face = faces.measure(frame, kp) if faces else None
            obs = head.update(t, kp, face, (frame.shape[1], frame.shape[0]))
            hands_bit = hands_filter.update(t, {'off': 1, 'near': 0, 'unknown': None}[label])
            head_bit = head_filter.update(t, head.raw_bit(obs))
            ears = [e for e in ((face.ear_l, face.ear_r) if face else ()) if e is not None]
            eye = eyes.update(t, obs.blink if obs.source == 'face' else None, obs.gaze_down or 0.0,
                              float(np.mean(ears)) if ears else None, obs.pitch_delta if obs.source == 'face' else None)
            body = slumper.update(t, kp)
            sleep_bits = [b for b in (eyes.raw_bit(eye), slumper.raw_bit(body)) if b is not None]
            sleep_bit = sleep_filter.update(t, (1 if 1 in sleep_bits else 0) if sleep_bits else None)
            asleep = bool(sleep_bit) and not args.no_sleep_bit
            # PHONE_USE is already confirmed/debounced by phone_events.EventTracker: no FlickerFilter on top.
            phone_bit = phone.update(t, frame, kp) if phone is not None else 0
            # Yawn: confirmed by its own sequence (mouth wide open, then a hand covers it); hands only when needed.
            yawn_state = yawner.update(t, face, lambda: yawn.hand_points(hand_finder.find(frame, kp))
                                       if hand_finder is not None and kp is not None else []) if yawner else None
            yawn_bit = yawn.YawnDetector.raw_bit(yawn_state) if yawn_state else 0
            flags = cv_flags.pack_flags(asleep=asleep, hands_off=bool(hands_bit), off_road=bool(head_bit),
                                        phone=bool(phone_bit), yawn=bool(yawn_bit))
            if args.stream:
                try:
                    print(cv_flags.format_record(t, flags, occ.count), file=stream_out, flush=True)
                except BrokenPipeError:      # the reader went away: stop cleanly
                    break
            for ev in machine.update(t, label):
                ev['wall_latency_ms'] = round((time.monotonic() - t0) * 1000)
                print(f'[t={t:6.2f}s] EVENT {json.dumps(ev)}', flush=True)
                if log:
                    log.write(json.dumps(ev) + '\n'); log.flush()
            if ocsv:
                off, unk = hands_off_flag(label)
                ocsv.write(f'{frame_no - 1},{t:.3f},{off},{unk}\n')
                ocsv.flush()
            if bcsv:
                bcsv.write(f'{frame_no - 1},{t:.3f},{flags},{hands_bit},{head_bit},{occ.count},{phone_bit},{yawn_bit}\n')
                bcsv.flush()
            if fcsv:
                cells = []
                for p in points:
                    cells += ['', '', '', ''] if p is None else [f'{p[0][0]:.0f}', f'{p[0][1]:.0f}',
                                                                   f'{p[1][0]:.0f}', f'{p[1][1]:.0f}']
                fcsv.write(f'{frame_no - 1},{t:.3f},{label},{states[0]},{states[1]},'
                           + ','.join(cells) + f',{int(machine.confirmed)},{dbg["sources"][0]},{dbg["sources"][1]},'
                           f'{"" if obs.yaw is None else f"{obs.yaw:.3f}"},'
                           f'{"" if obs.yaw_delta is None else f"{obs.yaw_delta:.3f}"},'
                           f'{"" if obs.pitch is None else f"{obs.pitch:.3f}"},'
                           f'{"" if obs.pitch_delta is None else f"{obs.pitch_delta:.3f}"},'
                           f'{obs.state},{obs.direction or ""},{obs.reason or ""},{obs.baseline_source},'
                           f'{obs.source or ""},{"" if obs.blink is None else f"{obs.blink:.3f}"},'
                           f'{"" if obs.gaze_down is None else f"{obs.gaze_down:.3f}"},'
                           f'{eye.state},{"" if eye.score is None else f"{eye.score:.3f}"},{eye.ref:.3f},'
                           f'{eye.closed_s:.2f},{"" if eye.onset_s is None else f"{eye.onset_s:.2f}"},'
                           f'{"" if eye.ear is None else f"{eye.ear:.3f}"},{eye.perclos:.3f},{eye.microsleeps},'
                           f'{int(eye.drowsy)},{body.state},{"" if body.ratio is None else f"{body.ratio:.3f}"},'
                           f'{body.slumped_s:.2f},'
                           f'{occ.count},{occ.zones["driver"]},{occ.zones["front"]},{occ.zones["rear"]},{occ.raw}\n')
            proc += 1
            lat.append(time.monotonic() - t0)
            if args.show or args.save:
                view = frame.copy()
                if wheel is not None:
                    cv2.polylines(view, [wheel], True, (255, 255, 0), 4)
                    for o in auto_wheel.tolerance_outline(wheel, margin, (view.shape[1], view.shape[0])):
                        cv2.polylines(view, [o], True, (255, 100, 0), 3)
                if kp is not None:
                    for a, b in rules_sk:
                        if kp[a, 2] >= rules.KP_CONF and kp[b, 2] >= rules.KP_CONF:
                            cv2.line(view, tuple(int(v) for v in kp[a, :2]), tuple(int(v) for v in kp[b, :2]),
                                     (255, 255, 255), 3)
                for st, p in zip(states, points):
                    if p is not None:
                        col = (0, 200, 0) if st == 'near' else (0, 0, 255)
                        cv2.circle(view, tuple(int(v) for v in p[0]), 12, (255, 255, 255), -1)       # wrist
                        cv2.line(view, tuple(int(v) for v in p[0]), tuple(int(v) for v in p[1]), col, 4)
                        cv2.circle(view, tuple(int(v) for v in p[1]), 20, col, -1)                    # hand point
                for lm in dbg['landmarks']:
                    if lm is not None:
                        pts, inside = lm
                        for a_, b_ in hands_mediapipe_connections():
                            cv2.line(view, tuple(int(v) for v in pts[a_]), tuple(int(v) for v in pts[b_]),
                                     (255, 255, 255), 2)
                        for q, on in zip(pts, inside):
                            cv2.circle(view, (int(q[0]), int(q[1])), 6, (0, 220, 0) if on else (0, 0, 255), -1)
                color = {'near': (0, 200, 0), 'off': (0, 0, 255), 'unknown': (160, 160, 160)}[label]
                cv2.putText(view, f'{label.upper()}  L:{states[0]}({dbg["sources"][0]}) '
                                  f'R:{states[1]}({dbg["sources"][1]})  t={t:.2f}s',
                            (30, 80), 0, 2.0, color, 5)
                if machine.confirmed:
                    cv2.putText(view, CASE, (30, 160), 0, 2.2, (0, 0, 255), 6)
                head_color = {'turned': (0, 0, 255), 'down': (0, 0, 255), 'up': (0, 0, 255),
                              'forward': (0, 200, 0), 'unknown': (160, 160, 160)}[obs.state]
                unit = ' deg' if obs.source == 'face' else ''
                if obs.state == 'unknown':
                    head_text = f'HEAD UNKNOWN ({obs.reason})'
                elif obs.state == 'turned':
                    head_text = f'HEAD TURNED {obs.direction}  yaw {obs.yaw_delta:+.0f}{unit}' if unit else \
                        f'HEAD TURNED {obs.direction}  yaw {obs.yaw_delta:+.2f}'
                elif obs.state in ('down', 'up'):
                    head_text = f'HEAD {obs.state.upper()}  pitch {obs.pitch_delta:+.0f}{unit}' if unit else \
                        f'HEAD {obs.state.upper()}  pitch {obs.pitch_delta:+.2f}'
                elif unit:
                    head_text = f'HEAD FORWARD  yaw {obs.yaw_delta:+.0f}  pitch {obs.pitch_delta:+.0f}{unit}'
                else:
                    head_text = f'HEAD FORWARD  yaw {obs.yaw_delta:+.2f}  pitch {obs.pitch_delta:+.2f}'
                head_text += f' [{obs.source or "-"}/{obs.baseline_source}]'
                if obs.blink is not None:
                    head_text += f'  blink {obs.blink:.2f}'
                cv2.putText(view, head_text, (30, 240), 0, 1.3, head_color, 4)
                cv2.putText(view, f'FLAGS 0x{flags:02X}  hands_off={hands_bit} eyes_off_road={head_bit} '
                                  f'asleep={int(bool(flags & cv_flags.ASLEEP))} phone={phone_bit} yawn={yawn_bit}',
                            (30, 320), 0, 1.5, (0, 0, 255) if flags else (200, 200, 200), 4)
                zone_color = {'driver': (255, 200, 0), 'front': (0, 200, 255), 'rear': (255, 0, 255)}
                for person in occ.people:
                    b = [int(v) for v in person.box]
                    cv2.rectangle(view, (b[0], b[1]), (b[2], b[3]), zone_color[person.zone], 4)
                    cv2.putText(view, f'{person.zone} {person.conf:.2f}', (b[0] + 8, b[1] + 40), 0, 1.3,
                                zone_color[person.zone], 3)
                cv2.putText(view, f'PEOPLE {occ.count}  (driver {occ.zones["driver"]}, front {occ.zones["front"]}, '
                                  f'rear {occ.zones["rear"]}; this frame {occ.raw})', (30, 500), 0, 1.4, (255, 255, 255), 4)
                eye_color = {'closed': (0, 0, 255), 'open': (0, 200, 0), 'unknown': (160, 160, 160)}[eye.state]
                eye_text = f'EYES {eye.state.upper()}'
                if eye.state == 'closed':
                    eye_text += f' {eye.closed_s:.1f}s' + ('' if eye.onset_s is None else f' (onset {eye.onset_s:.1f}s)')
                elif eye.raw_closed:
                    eye_text += ' (closing, maybe a blink)'
                if eye.score is not None:
                    eye_text += f'  score {eye.score:.2f} ref {eye.ref:.2f}'
                if eye.drowsy:
                    eye_text += f'  DROWSY (PERCLOS {eye.perclos:.0%}, {eye.microsleeps} long closures)'
                cv2.putText(view, eye_text, (30, 400), 0, 1.5, eye_color, 4)
                body_color = {'slumped': (0, 0, 255), 'upright': (0, 200, 0), 'unknown': (160, 160, 160)}[body.state]
                body_text = f'POSTURE {body.state.upper()}'
                if body.state == 'slumped':
                    body_text += f' {body.slumped_s:.1f}s' + (' (driver lost)' if body.held else '')
                if body.ratio is not None:
                    body_text += f'  head/shoulder {body.ratio:+.2f} (upright {body.baseline:.2f})'
                cv2.putText(view, body_text, (30, 580), 0, 1.4, body_color, 4)
                if eye.score is not None:                       # bar: score vs the closing level
                    cv2.rectangle(view, (30, 430), (430, 460), (80, 80, 80), 2)
                    cv2.rectangle(view, (30, 430), (30 + int(400 * min(eye.score, 1.0)), 460), eye_color, -1)
                    mark = 30 + int(400 * eyes_closed.ENTER)
                    cv2.line(view, (mark, 425), (mark, 465), (255, 255, 255), 3)
                if phone is not None and phone.last is not None:
                    ev, boxes, _ = phone.last
                    for p in boxes:
                        used = ev.state == 'POSITIVE' and ev.phone is not None and tuple(p[:4]) == tuple(ev.phone[:4])
                        col = (0, 0, 255) if used else (255, 0, 255)
                        cv2.rectangle(view, (int(p[0]), int(p[1])), (int(p[2]), int(p[3])), col, 4)
                        cv2.putText(view, f'phone {p[4]:.2f}', (int(p[0]), int(p[1]) - 10), 0, 1.2, col, 3)
                    cv2.putText(view, f'PHONE {"USE" if phone_bit else "-"}  evidence {ev.state} ({ev.reason})',
                                (30, 660), 0, 1.3, (0, 0, 255) if phone_bit else (200, 200, 200), 4)
                if yawn_state is not None:
                    jaw = '-' if yawn_state.jaw_open is None else f'{yawn_state.jaw_open:.2f}'
                    cv2.putText(view, f'YAWN {"YES" if yawn_bit else "-"}  jawOpen {jaw}'
                                      f'{"  " + yawn_state.reason if yawn_state.reason else ""}',
                                (30, 740), 0, 1.3, (0, 0, 255) if yawn_bit else (200, 200, 200), 4)
                if kp is not None and obs.yaw is not None:
                    for k in (eyes_off_road.NOSE, eyes_off_road.LEYE, eyes_off_road.REYE):
                        cv2.circle(view, (int(kp[k, 0]), int(kp[k, 1])), 9, head_color, -1)
                if args.save:
                    if writer is None:
                        writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*'mp4v'),
                                                 fps / args.stride, (view.shape[1], view.shape[0]))
                    writer.write(view)
                if args.show:
                    cv2.imshow('live', cv2.resize(view, None, fx=0.4, fy=0.4))
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
    finally:
        if reader is not None:
            reader.stop()
        if player:
            player.stop()
            player.join(timeout=2)
            print(f'player: read {player.read} frames, drawn {player.drawn}, skipped {player.late} (late); '
                  f'longest freeze {player.max_draw_gap:.2f}s', flush=True)
        for ev in machine.finish(t):
            print(f'[t={t:6.2f}s] EVENT {json.dumps(ev)}', flush=True)
            if log:
                log.write(json.dumps(ev) + '\n')
        if reader is None:
            cap.release()
        if writer:
            writer.release()
        if log:
            log.close()
        if fcsv:
            fcsv.close()
        if ocsv:
            ocsv.close()
        if bcsv:
            bcsv.close()
    if lat:
        mean = float(np.mean(lat))
        print(f'processed {proc} frames; mean {mean * 1000:.0f} ms/frame = {1 / mean:.1f} fps '
              f'(source {fps:.0f} fps, stride {args.stride}' + (f', {reader.read_count - proc} frames dropped to stay live' if reader else '') + f'); p95 {np.percentile(lat, 95) * 1000:.0f} ms')


if __name__ == '__main__':
    main()
