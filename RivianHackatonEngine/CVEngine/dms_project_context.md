# In-cabin driver monitoring (DMS) project: context summary

Paste this at the start of a new chat, and attach the `.py` files listed below if you want to continue the code.

## 1. Goal
Detect unsafe driver activity **in real time** from an in-cabin camera, for a **car application**:
using a phone, no seatbelt, hands not on the steering wheel, yawning, and more events to be added later.
The user wants to be able to add events later and be specific about detection logic and **output format**.

## 2. Input video
- Ceiling/center-mounted **fisheye** camera, 1920x1536, 30 fps, 30 s clip (`08_14_62_092916_video_inCabin.mp4`), static camera.
- Two people: driver on the **right** half, passenger on the left. Dim, low-contrast, daylight/overcast.
- A **second video** from a different car (different driver, different camera view and wheel position) exposed a problem with the hard-coded wheel zone (see section 7).
- Behaviors in video 1: phone at ear (call), looking at a dark phone in the lap, one hand off the wheel, leaning/turning away.

## 3. Decisions made
- **Architecture: several small pretrained models + rules** (not one end-to-end video classifier). Reasons: far less labeled data, each event is testable separately, easier to debug and certify, several events can happen at once.
- Models used in the prototype (YOLO11 from Ultralytics):
  - `yolo11m-pose.pt`: 17 COCO keypoints per person (nose, eyes, ears, shoulders, elbows, wrists, hips, knees, ankles).
  - `yolo11m.pt`: object detector; only COCO class 67 "cell phone" is used.
- **Licensing caveat:** Ultralytics YOLO is AGPL-3.0, so fine for prototyping but a commercial product needs a paid license or permissive alternatives:
  YOLOX / RTMDet / RTMPose (Apache-2.0, `rtmlib` or MMPose), MediaPipe (Apache-2.0), D-FINE / RT-DETR (original repos). Check weights and dataset licenses too.
- Target hardware is **not yet decided**. For production: automotive SoC NPU (Qualcomm SA8155/8295, NVIDIA Orin, TI TDA4, Ambarella CV2x, Renesas R-Car), INT8 quantization, ONNX export; keep alert logic as plain deterministic code. EU GSR2 requires driver drowsiness/attention warning; Euro NCAP has DMS protocols.
- Fisheye: either train on distorted frames from the same camera, or dewarp per seat (virtual perspective crop with OpenCV fisheye calibration). Not implemented yet.

## 4. What was built (files)
| File | Purpose |
|---|---|
| `download_models.py` | Downloads `yolo11m-pose.pt` and `yolo11m.pt` with resume/retry (the user's network resets GitHub downloads). |
| `extract.py` | Runs pose (imgsz 640) on every frame + phone detection (imgsz 640, conf 0.15) on a driver-area crop starting at (760, 600). Caches results to `out/cache.npz`. Even frames first, then odd. Slot 1 = driver (box center x > W/2), slot 0 = passenger. Config at top: `CX0, CY0`, `DRIVER_ON_RIGHT`. |
| `rules.py` | Rule engine: per-frame flags, temporal smoothing, event list. Writes `events.csv`, `frame_flags.csv`, `flags.npz`. Requires `wheel.json` from the same folder as the cache; missing or invalid calibration is an error. |
| `render.py` | Annotated video (frame number, skeleton, wheel zone, phone boxes, alerts) + a key-frame JPG per alert. |
| `live_phone.py` | Live PHONE_USE monitor: reads a camera/stream/video in order, always processes the newest frame (skipping frames while busy; `--stride N` for deterministic offline runs), detects phones (hybrid: `--dark-phone-model` on dark frames), builds per-frame evidence and confirmed events with `phone_events.py`, and optionally feeds `SafetySystem` (`--engine`). `--show` window, `--log` per-frame CSV, `--events` JSONL of start/end updates, `--record` video, `--no-hands` skips hand landmarks. |
| `phone_events.py` | PHONE_USE decision logic (one behavior for phone at ear and phone in hand). Per-frame evidence is POSITIVE/NEGATIVE/UNKNOWN: at ear = phone near the head with a hand at it; in hand = phone near a wrist that moves relative to the cabin (camera shake removed by `CabinMotion`), or a still phone while the driver looks down, only if the detection is confident (>= 0.3) and hand landmarks put >= 3 points on the phone. `EventTracker` (`PhoneUseTracker`) confirms: start with >= 3 POSITIVE frames, >= 60% of known frames and >= 1 s span in 2 s; end after 3 s without POSITIVE (with a NEGATIVE), after 3 s without observation, or at clip end. |
| `hands.py` | MediaPipe hand landmarks in an enlarged crop around each driver wrist (`requirements-hands.txt`, install with `--no-deps`); used by `phone_events.py` to check that fingers are on a still phone. Also has hands-off-wheel helpers (not used: the car has a sensor for that). |
| `phone_data.py`, `label_review.py`, `phone_drivers.json` | Phone-detector fine-tuning: frame selection with draft labels, driver-split dataset, local review window, synthetic dark copies, training and evaluation. See "Phone detector fine-tuning" below. |
| `draw_wheel.py` (not included) | Previously proposed manual calibration tool; unavailable in this checkout. |
| `download_wheel_models.py`, `auto_wheel.py` | Automatic wheel zone with YOLOE open-vocabulary segmentation ("steering wheel"), refined by majority agreement across reliable frames. Tested locally on `05_23_62_005853_video_inCabin.mp4`; broader validation is still required. |

All scripts use relative paths. Python 3.9 venv on the user's Mac (macOS, CPU), folder "rivian hackathon".

### Commands (per video)
```
python3 auto_wheel.py my_video.mp4 out/wheel.json     # or live: python3 auto_wheel.py --live 0 out/wheel.json
python3 extract.py my_video.mp4 out/cache.npz
python3 rules.py out/cache.npz
python3 render.py my_video.mp4 out/cache.npz
```
Install: `python3 -m pip install ultralytics opencv-python numpy scipy`. Check `out/wheel_preview.jpg` before trusting an auto-detected zone.

Rules and rendering use the saved polygon, not a hard-coded wheel location. Calibration must contain
`wheel` (at least three full-frame pixel `[x, y]` points) and `frame_size` (`[width, height]`).
New automatic calibrations specify `wrist_margin_px`: 15% of the wheel's shorter bounding-box
dimension, to account for wrists lying outside the rim while fingers touch it. Rules apply only this
saved margin. Older automatic zones without this field retain zero extra margin, and legacy manual
polygons retain the 60 px tolerance. Rendering rejects a calibration whose dimensions differ
from the video or whose polygon/margin differs from the saved flags; re-run rules after calibration changes.
Existing pose/phone caches can be reused without model inference.

Automatic calibration samples up to 16 distinct frames, rejects masks below confidence 0.15,
checks box consistency, and retains samples scoring at least 40% of the best consistent detection.
At least three reliable masks are required. A pixel must belong to a strict majority of the retained
masks to enter the consensus; the largest external contour is simplified without a convex hull or
outward dilation. Boxes alone are never accepted as wheel masks. This prevents a few oversized
detections from expanding the zone into the driver's torso or dashboard. The output records
`algorithm: "mask_majority_v1"` and the retained `sample_count`. Preview and video show the tight
wheel contour in cyan and the separate wrist-tolerance boundary in blue, so the accepted area is visible.
Existing calibrations are not automatically replaced: re-run `auto_wheel.py`, then rules and rendering,
to use the new algorithm. Retain the old output directory when comparing results.
The polygon still includes any enclosed interior: 2D wrist overlap is not proof of rim contact or grip,
and consistently wrong masks or an occluded rim can still produce incorrect calibration.

### Live wheel calibration (no future frames)
`python3 auto_wheel.py --live SOURCE [out/wheel.json]`, where SOURCE is a camera index, stream URL, or a
video file read sequentially to simulate a camera. `LiveWheelCalibrator` (in `auto_wheel.py`) is the
reusable component for a live pipeline: call `add_frame(frame, capture_timestamp_s)` for every frame.
- It segments at most one frame per `LIVE_INTERVAL_S` (1 s), so samples are spread out and cheap.
- After 16 samples, each new sample re-runs the same consensus and thresholds as video mode over a
  sliding window of the latest 16 samples. Thresholds are not lowered for live use.
- States: `CALIBRATING` (wheel monitoring unavailable), `LOCKED` (zone fixed; no further wheel
  inference), `FAILED` after `LIVE_TIMEOUT_S` (60 s) without consensus.
- A timestamp moving backwards (seek, clock jump) resets it. Frame-size changes are rejected.
- Output is the same `wheel.json` format with `mode: "live"`, `locked_after_s`, and `window_s`.
The camera and wheel are fixed per vehicle, so the intended live flow is: load a verified `wheel.json`
for this camera if one exists; otherwise calibrate at startup, save it, and reuse it on later drives.
While calibrating or failed, only wheel-based monitoring is unavailable; phone and face continue.

Simulated live on five clips (frames fed in order): the three clips accepted by video mode locked
after 15 s of footage, with zone IoU 0.89 / 0.99 / 0.88 against video mode; the two clips rejected by
video mode also stayed unlocked in live mode. `08_14_62_092916` locked with only 3 supporting samples,
the minimum: always check the preview before saving a live calibration for reuse.

### Phone detector fine-tuning
The generic COCO phone detector misses phones in dark footage and fires on sunglasses/hands at low
confidence (0.17). Brightening, higher resolution, hand crops, a lower threshold, YOLO11x, and YOLOE
"phone" prompts were tested and did not detect the dark phone. Fine-tuning on this camera is the next step.
- `phone_drivers.json` groups the 116 clips into 17 drivers (by appearance on the contact sheets; 60 clips
  were added later, matched to known drivers where possible, plus new drivers D16 and D17).
  Splits are by driver, never by frame. Current split: test D03 D04 D07 D13 (includes a dark driver,
  sunglasses false alarms, phone in lap, and the second session), val D05 D08 D09 D10 D16 (D05 moved from
  train after labeling: val had only 7 phone frames; D16 adds dark footage), train the rest
  (includes dark drivers D01, D02, D17).
- `python phone_data.py prelabel` sampled 1/s (3/s in dim clips) and stored YOLO11x draft boxes
  (4,710 frames for 116 clips). `build` selected 700 frames from the first 56 clips, then
  `build --only-new-clips` another 700 from the clips without frames (1,400 total).
- Images are the same driver-side crop (x >= 760, y >= 600) used live. `phone_dataset/` contains images
  of real people and is git-ignored; keep it local and label locally.
- Only frames confirmed in `label_review.py` are used for training or evaluation. Draft boxes are never
  ground truth.
- `darken` adds 2 synthetic dark copies of reviewed train frames (phones and an equal number of
  phone-free frames, so darkness alone does not mean phone). Judge dark performance on real dark test frames.
- `train` fine-tunes `yolo11m.pt` with the first 10 layers frozen (60 epochs). One epoch of 172 images
  took ~1.2 min on this CPU; the full set needs a GPU (~7 h estimated on CPU).
- `eval` compares the fine-tuned model with `yolo11m.pt` on reviewed test frames: phones found, images
  with false alarms, dark phones found, hand-at-face false alarms. Then use
  `live_phone.py --phone-model phone_dataset/runs/phone_ft/weights/best.pt`.
- First fine-tuning result (1,400 frames, best epoch 16 of 31): most labeled phones lie in the console tray
  (train: 230 tray-only frames vs 68 phones elsewhere, only 15 of those bright). The model learned tray
  phones and dark in-hand phones, but not bright in-hand phones (test D07/D13: 0/44 vs 23/44 for yolo11m).
  On dark in-hand phones of an unseen driver (val D16) it finds 56/71 vs 9/71 for yolo11m (conf >= 0.5).
  Current recommendation: `live_phone.py --dark-phone-model .../best.pt` uses the fine-tuned model only when
  the driver crop is dark (mean gray < 45, conf >= 0.5) and yolo11m otherwise. A second training round
  should add/oversample bright in-hand and at-ear phone frames so tray phones do not dominate.
- Remote GPU: `python phone_data.py pack` writes `phone_train_bundle.tar.gz` (reviewed frames only, labels,
  meta, `phone_data.py`, `live_phone.py`, `yolo11m.pt`, `requirements-gpu.txt`; git-ignored). On the GPU
  machine: unpack, `pip install -r requirements-gpu.txt`, then `darken`, `train --batch 32 --workers 8`,
  `eval`. Copy `phone_dataset/runs/phone_ft/` back into the local `phone_dataset/runs/`.

### PHONE_USE validation (all 116 clips, `--stride 8`, hybrid detector)
- Events in 8 clips after the fixes, all real phone use. All 97 clips without events before remain without events (the fixes
  only make evidence stricter). Removed false alarms: 010829 (lying tray phone, hand reaching, looking
  down), 014837 (steering-wheel grip pad detected as a phone at conf 0.15-0.28), 023436 9-29 s (dark object
  on the lap, hand on the wheel).
- Known misses: phones held still low by the console when MediaPipe finds no hand (084339), short use at
  the clip end (021132), and clips where the phone is not detected (011802, 015556, 025248, 025453,
  030915, 085155). The detector cannot tell a held phone from a lying one by itself; a second training round
  with separate "phone in hand" / "phone lying" classes is the planned fix.

## 5. Rules (in `rules.py`), thresholds tuned on ONE clip
- Scale = shoulder width in pixels. Keypoints below conf 0.35 are ignored.
- **Hand on wheel:** wrist inside the calibrated polygon or within its saved `wrist_margin_px` tolerance (legacy defaults described above). 0 hands = `HANDS_OFF_WHEEL`, 1 hand = `ONE_HAND_ON_WHEEL`.
- **PHONE_AT_EAR:** a detected phone center within 0.8 x shoulder width of ear/nose. A wrist near the face without a detected phone does not count (it is only reported as the diagnostic `hand_at_face`).
- **PHONE_IN_HAND:** phone box near a wrist and not near the head.
- **HEAD_DOWN_HAND_IN_LAP (pose-only fallback):** off-wheel wrist more than 0.85 shoulder widths below the shoulder line AND head pitch (nose y minus mean ear y, over shoulder width) above 0.17. Normal driving gave pitch 0.08-0.15.
- **Temporal logic:** median filter (9 frames), merge gaps up to 30 frames, minimum duration before an alert: 1.0 s for phone/head-down/hands-off, 2.0 s for one hand.
- Each event reports **onset_frame** (behavior first appears), **alert_frame** (confirmed after the minimum duration) and **end_frame**.
- There is no default polygon: each camera setup requires a validated `wheel.json`.

## 6. Results on video 1 (900 frames at 30 fps)
| Event | Onset | Alert | End |
|---|---|---|---|
| ONE_HAND_ON_WHEEL | 226 | 286 | 797 |
| HEAD_DOWN_HAND_IN_LAP | 241 | 271 | 354 |
| PHONE_AT_EAR | 370 | 400 | 714 |

## 7. Known problems and limitations
- **Wheel calibration must match the camera setup.** Rules and rendering now load per-camera `wheel.json`; same-resolution calibration from another camera still requires operator verification.
- Automatic wheel segmentation has only limited clip-level validation. The model needs a ~570 MB text encoder (`mobileclip_blt.ts`) and `pip install git+https://github.com/ultralytics/CLIP.git`. Earlier environments blocked those downloads; the local Windows test succeeded. Alternatives for difficult cameras: fine-tune a wheel segmentation model on labeled frames, or use validated manual calibration per vehicle (the wheel is fixed per mount).
- The generic phone detector **missed the dark phone in the lap** (dark phone, dark hoodie, even at conf 0.03). Needs a fine-tuned phone detector on this camera's images.
- **Missed event:** around frames 720-750 the driver holds the phone out in front of him; nothing flagged.
- Thresholds were tuned on a single clip and need re-tuning on more drivers.
- **Not implemented yet:** seatbelt, yawning/eyes closed (face landmarks, e.g. MediaPipe Face Mesh: mouth/eye aspect ratios), cigarette, passenger events, fisheye dewarping, real-time streaming, INT8 export.
- Speed: ~1.3 s per frame on 2 CPU cores for both models (sandbox); a real-time target needs a GPU/NPU, frame skipping with tracking, and smaller models.

## 8. Plan / next steps
1. Validate the wheel zone on both videos (auto or manual) and re-check events on video 2.
2. Collect more clips (different drivers, clothes, lighting, staged unsafe behavior); label phone, seatbelt (strap segmentation or torso-crop classifier) and optionally cigarette in CVAT / Label Studio, pre-labeling with pretrained models.
3. Fine-tune a permissively licensed detector (e.g. YOLOX-S) for phone/seatbelt. Split train/val by person or session, never by frame.
4. Add face-landmark yawning/drowsiness; add head-pose / "face not visible" state.
5. Swap pose to RTMPose-wholebody (Apache-2.0) via `rtmlib` for a product; export to ONNX, quantize to INT8 for the chosen SoC; measure false alarms per hour and per-event precision/recall.

## 9. How to add an event or change output
- New event = a rule in `frame_flags()` in `rules.py`, an entry in `MIN_DUR` (seconds before alert), optionally a color in `COL` in `render.py`.
- Outputs today: `events.csv`, `frame_flags.csv`, annotated mp4. Can be extended to JSON events (type, severity, confidence, frame range), a live event stream, per-event clips, or thresholds in a config file.
- When asking for a new event, give: the name, how it is defined (e.g. "no hand on the wheel for more than 3 s"), severity, and the output format.

## 10. Working preferences observed
- Wants to run heavy steps **locally** (not in the assistant's sandbox) to save tokens. Assistant should write code and give commands, not run model inference itself.
- Wants commands with `python3` (not `python`).
- Uses a Python 3.9 venv on macOS; GitHub downloads sometimes reset, so give resumable download scripts.
