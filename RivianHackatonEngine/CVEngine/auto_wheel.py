"""Finds the steering wheel automatically (open-vocabulary segmentation, no training) and saves wheel.json.
Usage: python3 auto_wheel.py my_video.mp4 [out/wheel.json]
It samples frames, segments "steering wheel" on each, takes the UNION of the confident detections, and writes
the zone + a preview image (wheel_preview.jpg) next to wheel.json. ALWAYS look at the preview. If it is wrong,
use draw_wheel.py (manual) instead.
"""
import cv2, json, os, sys, numpy as np
from ultralytics import YOLOE

VIDEO = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else 'out/wheel.json'
# ---- CONFIG ---------------------------------------------------------------
PROMPTS = ['steering wheel', 'car steering wheel']
N_FRAMES = 16                 # frames sampled evenly across the video
KEEP_FRAC = 0.4               # keep detections with at least this fraction of the best confidence
CONF = 0.01                   # low on purpose; we pick the best candidate ourselves
AREA_MIN, AREA_MAX = 0.01, 0.10   # accepted wheel size as a fraction of the image (a wheel is ~5%; rejects seats / cabin-wide hits)
MIN_IOU = 0.25                # a detection must overlap the median box of all frames at least this much (wheel does not move, false hits do)
MARGIN = 0.03                 # zone is grown by this fraction of image width so hands on the rim count
# ---------------------------------------------------------------------------

cap = cv2.VideoCapture(VIDEO)
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
model = YOLOE('yoloe-11m-seg.pt')
model.set_classes(PROMPTS, model.get_text_pe(PROMPTS))

kept = []; first_frame = None                  # kept: (confidence, mask, box) of the best plausible candidate per frame
for fi in np.linspace(0, N - 1, N_FRAMES + 2)[1:-1].astype(int):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(fi)); ok, f = cap.read()
    if not ok: continue
    if first_frame is None: first_frame = f.copy()
    r = model.predict(f, imgsz=960, conf=CONF, verbose=False)[0]
    cands = []
    for i, b in enumerate(r.boxes):
        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
        frac = (x2 - x1) * (y2 - y1) / (W * H)
        cands.append((float(b.conf), i, frac, [int(x1), int(y1), int(x2), int(y2)]))
    cands.sort(reverse=True)
    print('frame %d: top candidates (conf, box):' % fi, [(round(c[0], 3), c[3]) for c in cands[:3]])
    good = [c for c in cands if AREA_MIN <= c[2] <= AREA_MAX]
    if not good: print('   -> nothing with a plausible wheel size'); continue
    conf, i, frac, box = good[0]
    m = np.zeros((H, W), np.uint8)
    if r.masks is not None and i < len(r.masks.xy) and len(r.masks.xy[i]) >= 3:
        cv2.fillPoly(m, [r.masks.xy[i].astype(np.int32)], 1)
    else:
        cv2.rectangle(m, (box[0], box[1]), (box[2], box[3]), 1, -1)      # no mask -> use the box
    kept.append((conf, m, box))

if not kept:
    sys.exit('No wheel found in any sampled frame. Use draw_wheel.py to draw the zone by hand instead.')
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1])); inter = ix * iy
    return inter / float((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter + 1e-9)
ref = [float(v) for v in np.median(np.array([k[2] for k in kept]), axis=0)]       # median box over frames
kept = [k for k in kept if iou(k[2], ref) >= MIN_IOU]
if not kept:
    sys.exit('Detections disagree between frames. Use draw_wheel.py to draw the zone by hand instead.')
best = max(c for c, _, _ in kept)
used = [k for k in kept if k[0] >= KEEP_FRAC * best]
agree = np.zeros((H, W), np.uint8)
for _, m, _ in used: agree |= m                                           # union: partial detections add up to the full wheel
print('using %d of %d frames (confidence >= %.3f)' % (len(used), len(kept), KEEP_FRAC * best))
cnts, _ = cv2.findContours(agree, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
if not cnts: sys.exit('Empty wheel region. Use draw_wheel.py instead.')
hull = cv2.convexHull(max(cnts, key=cv2.contourArea))                     # fills the hole in the middle of the wheel
zone = np.zeros((H, W), np.uint8); cv2.fillPoly(zone, [hull], 1)
k = int(MARGIN * W); zone = cv2.dilate(zone, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
cnts, _ = cv2.findContours(zone, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
poly = cv2.approxPolyDP(max(cnts, key=cv2.contourArea), 0.01 * cv2.arcLength(max(cnts, key=cv2.contourArea), True), True)[:, 0, :]

os.makedirs(os.path.dirname(OUT) or '.', exist_ok=True)
json.dump({'wheel': [[int(x), int(y)] for x, y in poly], 'frame_size': [W, H], 'source': 'auto_wheel'}, open(OUT, 'w'))
prev = first_frame.copy()
for _, _, b in used: cv2.rectangle(prev, (b[0], b[1]), (b[2], b[3]), (0, 255, 255), 1)   # thin yellow = per-frame candidates
cv2.polylines(prev, [poly.astype(np.int32)], True, (255, 255, 0), 4)                    # thick cyan = final zone
pp = os.path.join(os.path.dirname(OUT) or '.', 'wheel_preview.jpg'); cv2.imwrite(pp, cv2.resize(prev, None, fx=0.5, fy=0.5))
print('saved %d points -> %s  (from %d frames)\nCHECK THE PREVIEW: %s' % (len(poly), OUT, len(used), pp))
