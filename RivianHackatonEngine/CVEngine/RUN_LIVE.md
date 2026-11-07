# How to run the live CV pipeline (`live.py`)

`live.py` reads a camera or a video, runs the detectors on each analyzed frame and produces the **flags byte**
for the AlertEngine / GameEngine. Everything here is run from `RivianHackatonEngine/CVEngine` with the CVEngine
Python environment active (on this machine: `venv\Scripts\Activate.ps1`).

For the full demo with the car app (backend + bridge + emulator) see the main [README](../../README.md),
section "Live CV test with the app (Windows)", and `run_commands.txt`.

## 1. Quick start

```powershell
cd RivianHackatonEngine\CVEngine
# watch a clip in real time with the detections drawn (q closes the window)
python live.py "videos\Hackathon dataset 2026\08_14_62_084339_video_inCabin.mp4" --realtime --show --no-hands
# same, faster models (one-time: pip install openvino; python export_openvino.py)
python live.py "videos\Hackathon dataset 2026\08_14_62_084339_video_inCabin.mp4" --realtime --show --no-hands `
    --pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/
# webcam (camera index 0); a camera is always live
python live.py 0 --show --no-hands
```

Video paths contain spaces: keep the quotes. The first start downloads the MediaPipe face/hand models
(a few MB); model loading takes ~10 s before the first frame.

## 2. The three ways to read a video

| Mode | Options | What happens | Use it for |
|---|---|---|---|
| **Offline** (default for a file) | none | every `--stride`-th frame is analyzed, as fast as the CPU allows; the video runs slower than real time (slow motion with `--show`) | repeatable tests, CSV outputs, comparing settings |
| **Live** | `--realtime` (a camera is always live) | the video runs at its real speed; the analysis always takes the **newest** frame and drops the ones it cannot keep up with, so it never lags behind | judging real-time behaviour, demos with overlays |
| **Live with smooth video** | `--play --realtime` | a separate player shows the plain video smoothly (no overlays); the analysis runs alongside | the bridge / car-app demo |

## 3. Choose what is detected (bits of the flags byte)

| Bit | Behaviour | On by default | Turn off | Needs |
|---|---|---|---|---|
| `0x80` | asleep: eyes closed or slumped posture | yes | `--no-sleep-bit` (still drawn and logged) | face model for eyes |
| `0x40` | eyes off the road (head turned / down / up) | yes | – | `--head face` (default) or `--head pose` |
| `0x20` | both hands off the steering wheel | yes | `--no-hands` (also skips the wheel calibration at start-up) | wheel calibration + hand models |
| `0x10` | phone use (at the ear or in the hand) | yes | `--no-phone` | phone detector |
| `0x08` | yawn (mouth opens wide, then a hand covers it) | yes | `--no-yawn` | `--head face` |

The car has a hands-on-wheel sensor, so **`--no-hands` is recommended**: it is faster and phone/head detection
start on the first frame instead of after ~2 s of wheel calibration.

Other detection options:
- `--head face|pose`: head direction from the MediaPipe face model (default, more accurate) or from pose keypoints only (faster).
- `--forward-start`: eyes-off-road takes the driver's most frequent head pose of the first 4 s as "looking at the road"
  and freezes it (nothing reported in the first 2 s). Good for short clips that start with the driver looking ahead;
  a driver looking away during the first seconds breaks it. See [ALGORITHM.md](ALGORITHM.md).
- `--method hybrid|pose|mediapipe`: how hands are judged on/off the wheel (only without `--no-hands`).
- `--dark-phone-model FILE`: fine-tuned phone model for dark frames. Used automatically if
  `phone_dataset/runs/phone_ft/weights/best.pt` exists (it is not in git).

## 4. Speed

Measured on an 8-core Intel CPU without GPU (details in [BENCHMARKS.md](BENCHMARKS.md)):

| Setup | Analyzed frames per second |
|---|---|
| default models, hands-off detection on | ~2.5 |
| default models, `--no-hands` | ~3.5 |
| `--no-hands` + OpenVINO models (below) | **~6.2** (~5.7 with `--show`) |

- **OpenVINO** (faster on Intel CPUs, same phone results on 114 of 116 clips): `pip install openvino`, run
  `python export_openvino.py` once, then add
  `--pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/`.
- Smaller models: `--pose-model yolo11s-pose.pt` is fine; **do not shrink the phone detector**
  (`yolo11s.pt`/`yolo11n.pt` or `--phone-size` below 640 miss phones held in the hand).
- `--stride N` analyzes at most every Nth frame (offline: exactly every Nth). Higher = less CPU.
- Run only **one** `live.py` at a time: several copies share the CPU and each gets slower.
- Drawing (`--show`) costs only ~2 %; the two YOLO models are ~94 % of the time.

## 5. Outputs

| Option | Output |
|---|---|
| `--show` | window with the detections (skeleton, phone boxes, head, eyes, yawn, flags) |
| `--text-scale X` | size of the overlay text (default 0.6; 1.0 = the original large text) |
| `--save out.mp4` | the same overlay video saved to a file |
| `--stream` | one line per analyzed frame on stdout: `timestamp,flags,occupants` (what the bridge reads) |
| `--flags x.csv` | per frame: `frame,time_s,flags,hands_off,eyes_off_road,occupants,phone,yawn` |
| `--frames x.csv` | detailed per-frame data (hands, head angles, eyes, posture, occupants) |
| `--events x.jsonl`, `--output x.csv` | hands-off-wheel events / simple hands-off CSV |

The last line printed is the speed: `processed N frames; mean X ms/frame = Y fps (... frames dropped to stay live)`.

**See which alerts the engines would give** for a run (from `RivianHackatonEngine`):
```powershell
python CVEngine\live.py "CVEngine\videos\Hackathon dataset 2026\05_23_62_010741_video_inCabin.mp4" --stride 8 --no-hands --flags CVEngine\x.csv
python main.py --cv CVEngine\x.csv
```
Or live, with the video window and alerts printed as they happen:
```powershell
python live_test.py "CVEngine\videos\Hackathon dataset 2026\05_23_62_010741_video_inCabin.mp4" --stride 8 --no-hands
```

## 6. Through the bridge / car app

The bridge (`python -m simulated_game_engine --cv VIDEO`, run from `RivianHackatonEngine`) starts `live.py` itself
with `--stream --wait --play --realtime`. Its options map to `live.py` options:

| Bridge | live.py |
|---|---|
| `--cv-no-hands` / `--cv-no-phone` / `--cv-no-yawn` | `--no-hands` / `--no-phone` / `--no-yawn` |
| `--cv-light` | `--method pose --head pose` |
| `--cv-pose-model X` / `--cv-phone-model X` | `--pose-model X` / `--phone-model X` |
| `--cv-stride N` (default 6 = at most 5 analyses per second at 30 fps) | `--stride N`; use `--cv-stride 4` or lower to let OpenVINO reach ~6 fps |
| `--cv-python PATH` | Python used for `live.py` (default: the engine's `.venv`, else the bridge's own) |

Example (fast setup, no hands-off detection):
```powershell
python -m simulated_game_engine --cv "CVEngine\videos\Hackathon dataset 2026\08_14_62_084339_video_inCabin.mp4" `
    --cv-no-hands --cv-stride 4 --cv-pose-model yolo11s-pose_openvino_model/ --cv-phone-model yolo11m_dyn_openvino_model/
```

## 7. Common problems

| Symptom | Cause / fix |
|---|---|
| `can't open file ...\live.py` | run from `RivianHackatonEngine\CVEngine` (or give the full path) |
| video plays in slow motion | offline mode: add `--realtime` |
| fps much lower than expected | another `live.py` (or a test) is using the CPU; run one at a time |
| `W0000 ... Feedback manager ...`, `NORM_RECT without IMAGE_DIMENSIONS` | harmless MediaPipe / TensorFlow Lite log lines |
| no phone alerts in dark clips | the fine-tuned dark model (`phone_dataset/.../best.pt`) is missing on this machine |
| eyes-off-road keeps firing for a driver who looks ahead | with `--forward-start` the driver looked away while the baseline was learned (first 4 s); try without it |
