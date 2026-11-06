"""Step 3: annotated video (frame number, skeleton, wheel zone, phones, alerts) + a key frame per alert.
Usage: python3 render.py my_video.mp4 [out/cache.npz]
"""
import cv2, numpy as np, csv, subprocess, sys, os
import rules
from auto_wheel import tolerance_outline

VIDEO = sys.argv[1]
CACHE = sys.argv[2] if len(sys.argv) > 2 else 'out/cache.npz'
OUT = os.path.dirname(CACHE) or '.'
P, PH, done = rules.load(CACHE)
with np.load(os.path.join(OUT, 'flags.npz')) as saved_flags:
    fl = {name: saved_flags[name] for name in saved_flags.files}
with open(os.path.join(OUT, 'events.csv')) as f:
    events = list(csv.DictReader(f))
SK = [(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(0,1),(0,2),(1,3),(2,4)]
COL = {'PHONE_AT_EAR':(0,0,255),'PHONE_IN_HAND':(0,128,255),'HEAD_DOWN_HAND_IN_LAP':(0,0,255),
       'HANDS_OFF_WHEEL':(0,0,255),'ONE_HAND_ON_WHEEL':(0,200,255)}

cap = cv2.VideoCapture(VIDEO)
W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
wheel, margin = rules.load_wheel(CACHE, frame_size=(W, H))
if ('wheel' not in fl or 'wheel_margin' not in fl
        or not np.array_equal(fl['wheel'], wheel) or fl['wheel_margin'].item() != margin):
    cap.release()
    sys.exit('Wheel calibration differs from the saved flags. Run rules.py again before rendering.')
S = 960 / W
wheel_tolerance = tolerance_outline(wheel, margin, (W, H))
def draw(f, i):
    if wheel_tolerance:
        cv2.polylines(f, wheel_tolerance, True, (255,100,0), 2)
    cv2.polylines(f, [wheel], True, (255,255,0), 3)
    for s, c in ((0,(255,160,0)),(1,(0,255,0))):
        k = P[i, s]
        if np.isnan(k[:,0]).all(): continue
        for a, b in SK:
            if k[a,2] > .3 and k[b,2] > .3: cv2.line(f, tuple(k[a,:2].astype(int)), tuple(k[b,:2].astype(int)), c, 3)
        for j in range(17):
            if k[j,2] > .3: cv2.circle(f, tuple(k[j,:2].astype(int)), 6, c, -1)
    for p in PH[i]:
        if not np.isnan(p[0]):
            cv2.rectangle(f, (int(p[0]),int(p[1])), (int(p[2]),int(p[3])), (255,0,255), 3)
            cv2.putText(f, 'phone %.2f' % p[4], (int(p[0]), int(p[1])-8), 0, 0.9, (255,0,255), 2)
    cv2.rectangle(f, (0,0), (W,110), (0,0,0), -1)
    cv2.putText(f, 'frame %d   t=%.2fs' % (i, i/30), (20,50), 0, 1.5, (255,255,255), 3)
    n = 0
    for k in rules.MIN_DUR:
        st = fl[k][i]
        if st == 0: continue
        txt, col = (k + ' !', COL[k]) if st == 2 else (k + ' (pending)', (160,160,160))
        cv2.putText(f, txt, (20 + n*640, 95), 0, 1.0, col, 3); n += 1
    nh = fl['nh'][i]
    cv2.putText(f, 'hands on wheel: %s' % (nh if nh >= 0 else '?'), (W-540, 50), 0, 1.2, (255,255,255), 3)
    cv2.putText(f, f'Cyan: wheel | Blue: wrist tolerance ({margin}px)',
                (20,H-20), 0, 0.9, (255,255,255), 2)
    return f

raw_path, final_path = os.path.join(OUT, 'annotated_raw.mp4'), os.path.join(OUT, 'annotated.mp4')
w = cv2.VideoWriter(raw_path, cv2.VideoWriter_fourcc(*'mp4v'), 30, (int(W*S), int(H*S)))
keys = {int(e['alert_frame']): e['event'] for e in events}
i = 0
while True:
    ok, f = cap.read()
    if not ok or i >= len(P): break
    f = draw(f, i)
    if i in keys: cv2.imwrite(os.path.join(OUT, 'key_%04d_%s.jpg' % (i, keys[i])), cv2.resize(f, None, fx=0.5, fy=0.5))
    w.write(cv2.resize(f, None, fx=S, fy=S)); i += 1
w.release(); cap.release()
try:   # re-encode to a widely playable H.264 file if ffmpeg is installed
    subprocess.run(['ffmpeg','-y','-v','error','-i',raw_path,'-c:v','libx264','-preset','veryfast','-crf','26','-pix_fmt','yuv420p',final_path], check=True)
    os.remove(raw_path); print('wrote', final_path)
except (FileNotFoundError, subprocess.CalledProcessError):
    print('ffmpeg not found; video is at', raw_path, '(open with VLC)')
print('rendered', i, 'frames')
