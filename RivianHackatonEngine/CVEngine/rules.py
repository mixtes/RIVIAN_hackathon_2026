"""Step 2: rules on the cached keypoints/phone boxes -> events.csv (with frame numbers) + frame_flags.csv.
Usage: python3 rules.py [out/cache.npz]
Requires wheel.json next to the cache; no camera-specific fallback is used.
"""
import numpy as np, cv2, csv, sys, os, json
from scipy.ndimage import median_filter

FPS = 30
# ---- CONFIG (adjust for your camera / car) ---------------------------------
WHEEL_MARGIN = 60          # legacy manual-zone tolerance; newer calibrations specify their own
KP_CONF = 0.35             # ignore keypoints below this confidence
LAP_MIN, PITCH_MIN = 0.85, 0.17     # lap / head-down thresholds (tuned on ONE clip -> re-tune)
MIN_DUR = {'PHONE_AT_EAR': 1.0, 'PHONE_IN_HAND': 1.0, 'HEAD_DOWN_HAND_IN_LAP': 1.0,
           'HANDS_OFF_WHEEL': 1.0, 'ONE_HAND_ON_WHEEL': 2.0}     # seconds before an alert is raised
# ---------------------------------------------------------------------------
NOSE, LEAR, REAR, LSHO, RSHO, LWR, RWR = 0, 3, 4, 5, 6, 9, 10

def load(cache):
    with np.load(cache) as d:
        return d['P'], d['PH'], d['done']

def load_wheel(cache, frame_size=None):
    path = os.path.join(os.path.dirname(cache) or '.', 'wheel.json')
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f'Missing wheel calibration: {path}. Run auto_wheel.py for this video first.')
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f'Invalid wheel calibration in {path}: expected an object.')
    size = np.asarray(data.get('frame_size'))
    if (size.shape != (2,) or not np.issubdtype(size.dtype, np.number)
            or not np.isfinite(size).all() or (size <= 0).any()
            or not np.equal(size, np.floor(size)).all()):
        raise ValueError(f'Invalid frame_size in {path}: expected positive integer [width, height].')
    if frame_size is not None and not np.array_equal(size, frame_size):
        raise ValueError(f'Wheel calibration dimensions {size.tolist()} do not match '
                         f'video dimensions {list(frame_size)}. Recalibrate for this video.')
    points = np.asarray(data.get('wheel'))
    if (points.ndim != 2 or points.shape[1] != 2 or len(points) < 3
            or not np.issubdtype(points.dtype, np.number)
            or not np.isfinite(points).all() or (points < 0).any()
            or (points > size).any()):
        raise ValueError(f'Invalid wheel polygon in {path}: expected at least three in-frame [x, y] points.')
    wheel = np.rint(points).astype(np.int32)
    if cv2.contourArea(wheel) <= 0:
        raise ValueError(f'Invalid wheel polygon in {path}: polygon has no area.')
    margin = data.get('wrist_margin_px', 0 if data.get('source') == 'auto_wheel' else WHEEL_MARGIN)
    if (isinstance(margin, bool) or not isinstance(margin, (int, float))
            or not np.isfinite(margin) or margin != int(margin) or not 0 <= margin <= min(size)):
        raise ValueError(f'Invalid wrist_margin_px in {path}: expected a nonnegative integer '
                         'no greater than the smaller frame dimension.')
    return wheel, int(margin)

def on_wheel(pt, wheel, margin):
    return cv2.pointPolygonTest(wheel.reshape(-1,1,2), (float(pt[0]), float(pt[1])), True) >= -margin

def frame_flags(kp, phones, wheel, margin):
    if np.isnan(kp[:,0]).all(): return None
    ok = lambda i: kp[i,2] >= KP_CONF
    sw = np.hypot(*(kp[LSHO,:2]-kp[RSHO,:2])) if ok(LSHO) and ok(RSHO) else 350.0   # shoulder width = scale
    wr = [kp[i,:2] for i in (LWR, RWR) if ok(i)]
    ears = [kp[i,:2] for i in (LEAR, REAR, NOSE) if ok(i)]
    n_wheel = sum(on_wheel(w, wheel, margin) for w in wr)
    wrist_ear = any(np.hypot(*(w-e)) < 0.65*sw and not on_wheel(w, wheel, margin) for w in wr for e in ears)
    ph = [p for p in phones if not np.isnan(p[0])]
    centers = [((p[0]+p[2])/2, (p[1]+p[3])/2) for p in ph]
    phone_near_head = any(np.hypot(c[0]-e[0], c[1]-e[1]) < 0.8*sw for c in centers for e in ears)
    phone_near_wrist = any(np.hypot(c[0]-w[0], c[1]-w[1]) < 0.6*sw for c in centers for w in wr)
    # pose-only fallback for a phone the detector cannot see: wrist far below shoulders AND head tilted down
    pitch = np.nan
    ear_y = [kp[i,1] for i in (LEAR, REAR) if ok(i)]
    if ear_y and ok(NOSE): pitch = (kp[NOSE,1] - np.mean(ear_y)) / sw
    lap = False
    if ok(LSHO) and ok(RSHO):
        sy = (kp[LSHO,1] + kp[RSHO,1]) / 2
        lap = any(((w[1]-sy)/sw) > LAP_MIN and not on_wheel(w, wheel, margin) for w in wr)
    return {
        'HEAD_DOWN_HAND_IN_LAP': bool(lap and pitch > PITCH_MIN),
        'n_hands_on_wheel': n_wheel,
        'PHONE_AT_EAR': bool(phone_near_head or wrist_ear),
        'PHONE_IN_HAND': bool(phone_near_wrist and not phone_near_head and not wrist_ear),
        'HANDS_OFF_WHEEL': n_wheel == 0 and len(wr) > 0,
        'ONE_HAND_ON_WHEEL': n_wheel == 1,
    }

def segments(b, min_gap=FPS):
    idx = np.flatnonzero(b)
    if len(idx) == 0: return []
    segs, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - p > min_gap: segs.append((s, p)); s = i
        p = i
    segs.append((s, p)); return segs

def main(cache):
    out = os.path.dirname(cache) or '.'
    wheel, margin = load_wheel(cache)
    P, PH, done = load(cache); N = len(P); names = list(MIN_DUR)
    raw = {k: np.zeros(N, bool) for k in names}; nh = np.full(N, -1); last = None
    for i in range(N):
        if done[i]: last = frame_flags(P[i,1], PH[i], wheel, margin)
        if last is None: continue
        nh[i] = last['n_hands_on_wheel']
        for k in names: raw[k][i] = last[k]
    smooth = {k: median_filter(raw[k].astype(np.uint8), size=9).astype(bool) for k in names}
    events = []
    for k in names:
        for s, e in segments(smooth[k]):
            dur = (e - s + 1) / FPS
            if dur >= MIN_DUR[k]:
                alert = min(s + int(MIN_DUR[k]*FPS), e)
                events.append(dict(event=k, onset_frame=int(s), alert_frame=int(alert), end_frame=int(e),
                                   onset_s=round(float(s)/FPS,2), alert_s=round(float(alert)/FPS,2),
                                   end_s=round(float(e)/FPS,2), duration_s=round(float(dur),2)))
    events.sort(key=lambda r: r['onset_frame'])
    cols = ['event','onset_frame','alert_frame','end_frame','onset_s','alert_s','end_s','duration_s']
    with open(os.path.join(out,'events.csv'),'w',newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(events)
    with open(os.path.join(out,'frame_flags.csv'),'w',newline='') as f:
        w = csv.writer(f); w.writerow(['frame','time_s','hands_on_wheel']+names)
        for i in range(N): w.writerow([i, round(i/FPS,3), nh[i]] + [int(smooth[k][i]) for k in names])
    state = {k: np.zeros(N, np.uint8) for k in names}      # 0 none, 1 started (pending), 2 alert raised
    for e in events:
        state[e['event']][e['onset_frame']:e['end_frame']+1] = 1
        state[e['event']][e['alert_frame']:e['end_frame']+1] = 2
    np.savez(os.path.join(out,'flags.npz'), nh=nh, wheel=wheel, wheel_margin=margin, **state)
    print(f'frames processed: {int(done.sum())}/{N}')
    for e in events: print(e)

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'out/cache.npz')
