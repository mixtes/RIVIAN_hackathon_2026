"""Step 1: run pose + phone detection on every frame and cache the results.
Usage: python3 extract.py my_video.mp4 [out/cache.npz]
"""
import cv2, numpy as np, sys, os, time
from ultralytics import YOLO

VIDEO = sys.argv[1]
CACHE = sys.argv[2] if len(sys.argv) > 2 else 'out/cache.npz'
os.makedirs(os.path.dirname(CACHE) or '.', exist_ok=True)

# ---- CONFIG (adjust for your camera) --------------------------------------
POSE_MODEL, DET_MODEL = 'yolo11m-pose.pt', 'yolo11m.pt'   # downloaded automatically on first run
CX0, CY0 = 760, 600        # top-left corner (px) of the driver area where phones are searched for
DRIVER_ON_RIGHT = True     # False for a right-hand-drive car / mirrored camera
POSE_SIZE, DET_SIZE = 640, 640
# ---------------------------------------------------------------------------

pose, det = YOLO(POSE_MODEL), YOLO(DET_MODEL)
cap = cv2.VideoCapture(VIDEO)
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); cap.release()
P  = np.full((N, 2, 17, 3), np.nan, np.float32)   # slot 0 = passenger, slot 1 = driver; 17 keypoints x,y,conf
PB = np.full((N, 2, 4), np.nan, np.float32)
PH = np.full((N, 3, 5), np.nan, np.float32)       # up to 3 phones per frame: x1,y1,x2,y2,conf
done = np.zeros(N, bool)

def stream():                                      # even frames first, then odd, so partial results are usable early
    for parity in (0, 1):
        c = cv2.VideoCapture(VIDEO)
        for i in range(N):
            ok, f = c.read()
            if not ok: break
            if i % 2 == parity: yield i, f
        c.release()

t0 = time.time()
for n, (i, f) in enumerate(stream()):
    r = pose(f, imgsz=POSE_SIZE, conf=0.25, verbose=False)[0]
    for b, k in zip(r.boxes.xyxy.cpu().numpy(), r.keypoints.data.cpu().numpy()):
        right = (b[0] + b[2]) / 2 > W / 2
        s = 1 if right == DRIVER_ON_RIGHT else 0
        area = (b[2]-b[0]) * (b[3]-b[1])
        if np.isnan(PB[i, s, 0]) or area > (PB[i,s,2]-PB[i,s,0]) * (PB[i,s,3]-PB[i,s,1]):
            PB[i, s] = b; P[i, s] = k
    d = det(f[CY0:, CX0:], imgsz=DET_SIZE, conf=0.15, classes=[67], verbose=False)[0]   # COCO class 67 = cell phone
    for j, (b, c) in enumerate(zip(d.boxes.xyxy.cpu().numpy(), d.boxes.conf.cpu().numpy())):
        if j < 3: PH[i, j] = [b[0]+CX0, b[1]+CY0, b[2]+CX0, b[3]+CY0, c]
    done[i] = True
    if n % 25 == 0:
        print(f'{n}/{N} frames, {time.time()-t0:.0f}s', flush=True)
        np.savez(CACHE, P=P, PB=PB, PH=PH, done=done)
np.savez(CACHE, P=P, PB=PB, PH=PH, done=done)
print('done in', round(time.time()-t0), 's ->', CACHE)
