# CVEngine benchmarks (speed and accuracy)

All numbers measured in this repo on the hackathon dataset (116 clips, 30 s each, 1920x1536, 30 fps, 17 drivers).
Hardware unless noted: **cloud VM, Intel Xeon Platinum 8573C, 8 vCPU, no GPU** (everything on CPU).
Commands are run from `RivianHackatonEngine/CVEngine`.

## 1. Where the time goes (one analyzed frame, current default models, `--no-hands`)

| Stage | ms | Share |
|---|---|---|
| Phone detector (YOLO11m, driver-area crop, 640 px) | 145 | 49 % |
| Pose model (YOLO11m-pose, 640 px) | 134 | 45 % |
| Face model (MediaPipe Face Landmarker) | 13 | 4 % |
| Rules / geometry (distances, angles, head, yawn) | 0.5 | 0.2 % |
| Drawing the overlay (only with `--show` / `--save`) | 6 | 2 % |
| **Total** | **298** | **3.4 fps** |

The two YOLO networks are 94 % of the time. The rules cost nothing.

## 2. Faster models: speed vs accuracy

Single model, 36 frames of clip 084339 (phone held still in the hand the whole time = the hardest phone case):

| Phone detector | ms | Phone found | | Pose model | ms | Person found |
|---|---|---|---|---|---|---|
| YOLO11m PyTorch 640 (default) | 139 | 19/36 | | YOLO11m-pose PyTorch (default) | 152 | 36/36 |
| YOLO11m PyTorch 480 | 86 | 8/36 | | YOLO11m-pose OpenVINO | 141 | 36/36 |
| YOLO11m PyTorch 320 | 52 | 0/36 | | YOLO11s-pose PyTorch | 115 | 36/36 |
| YOLO11m OpenVINO, fixed 640x640 | 94 | 14/36 | | **YOLO11s-pose OpenVINO** | **72** | 36/36 |
| **YOLO11m OpenVINO, dynamic shape** | **87** | **19/36** | | | | |
| YOLO11s PyTorch | – | 6/36 | | | | |
| YOLO11n PyTorch | – | 2/36 | | | | |

Full live pipeline (`live.py --no-hands --stride 8`), 3 clips (084339 phone held still, 092916 phone use, 014837 trap):

| Pose + phone model | ms/frame | fps | PHONE_USE frames 084339 / 092916 / 014837 |
|---|---|---|---|
| m + m (default) | 283-297 | 3.5 | 40 / 35 / 0 |
| s + s | 137-140 | 7.2 | **0** / 44 / 0 (held phone missed) |
| n + n | 90 | 11.1 | **0** / 50 / 0 (held phone missed) |
| s pose + m phone | 221 | 4.5 | 39 / – / – |
| m pose + s phone | 213 | 4.7 | **0** / – / – |
| **OpenVINO: s pose + m phone (dynamic)** | **158-162** | **6.2** | **40 / 35 / 0 (identical)** |

With a window (`--realtime --show`): 177 ms = 5.7 fps, 155 of 900 frames analyzed in 30 s (default: ~100).

Conclusions: the phone is small in the image, so the phone detector must stay large and at full resolution;
the pose model can be small. OpenVINO (Intel's CPU inference engine) gives 1.8x with no accuracy loss.
Use: `python export_openvino.py` once, then
`live.py ... --pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/`.

## 3. Real-time behaviour

| Test | Before | After |
|---|---|---|
| 30 s clip played at real speed, every frame analyzed | 267 s (falls 4 min behind) | ~37 s incl. ~7 s model loading |
| Same, every 8th frame | 50 s | ~37 s |
| Hands-off-wheel detection on vs `--no-hands` | 403 ms/frame, first phone alert 3.7 s | 281 ms/frame, first phone alert 1.1 s |
| 640x480 input (webcam size, same clip scaled) | – | 258 ms/frame, PHONE_USE detected |

Live sources keep only the newest frame and drop the rest, so output never lags behind the camera.
All detectors are causal (they use only the current and past frames).

## 4. Detection quality (dataset, `--stride 8` = ~3.75 analyzed fps)

| Behaviour | Result |
|---|---|
| PHONE_USE | All 116 clips: **0 clips with false alarms**; real use detected in 11 clips; 6 clips missed (phone not detected by the model). Removed false alarms: phone lying in the tray (010829), wheel grip pad (014837), dark object on the lap (023436) |
| PHONE_USE timing | start: median **1.07 s** after use begins; end: ~3 s after the phone is put down (bridges detector misses: 81 % of gaps between positive frames are <= 0.3 s, 99 % <= 3 s) |
| YAWN (mouth opens wide, then hand covers it) | Fires on **2 / 2** clearly visible yawns, **0** of the other 114 clips (21 other hand-at-mouth episodes: chin rest, smoking, eating, coughing, face rubbing) |
| Fine-tuned dark phone model | Dark in-hand phones of an unseen driver: **56/71** found vs 9/71 for the stock model |

## 5. All-116-clip sweep with the fast OpenVINO setup

`live.py --stride 8 --no-hands --pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/`
on all 116 clips, compared with the default models (YOLO11m pose + phone, PyTorch):

| | Result |
|---|---|
| PHONE_USE, clip-level (event yes/no) | **114 / 116 identical**. The 2 differences are both clips with real use: 021132 gains a 0.3 s event at the clip end, 023609 (picks the phone up, leaves the car) is missed. **No new false alarms** |
| PHONE_USE events | 12 in 11 clips |
| YAWN | 1 of the 2 visible yawns (084539; 010329 missed with the small pose model, found with the default) |
| EYES_CLOSED (teammate's detector) | 66 occurrences in 46 clips (not checked against labels) |
| EYES_OFF_ROAD (teammate's detector, before the forward-start change) | 353 occurrences in all 116 clips (staged clips, drivers look around a lot; not checked against labels) |
| Speed | 6.2 fps alone. Running several pipelines at once shares the 8 cores: with 4 in parallel each got ~630 ms/frame, and a 5th live run dropped to 2.9 fps |

## 6. Reproduce

- Full pipeline speed: `python live.py VIDEO --stride 8 --no-hands [--pose-model ... --phone-model ...]`; the last line
  prints ms/frame and fps. Run one at a time; parallel runs slow each other down.
- Engine alerts for a run: `python live.py VIDEO --stride 8 --flags x.csv`, then from `RivianHackatonEngine`:
  `python main.py --cv CVEngine\x.csv`.