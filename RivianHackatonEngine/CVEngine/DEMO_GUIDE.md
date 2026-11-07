# DriveScore live driver-monitoring demo: how to run it

This runs our computer-vision pipeline on a cabin video **as if it were a live camera** and shows what it detects,
frame by frame, in a window. At the end the terminal prints how fast it ran and what was detected.
No GPU, car app or server is needed: one Python environment. The demo videos come with the repository.

---

## 1. What you need

| | |
|---|---|
| Computer | Windows 10/11 (tested). macOS / Linux also work with the path changes noted below |
| Python | **3.13** (tested). Download from python.org; tick "Add python.exe to PATH" |
| Disk / internet | about 3 GB for the packages; the first run downloads the AI models (about 150 MB) |
| GPU | not needed (everything runs on the CPU) |
| Videos | included in the repository: `RivianHackatonEngine\CVEngine\use_this_videos\` |

## 2. One-time setup (PowerShell)

```powershell
cd <repo>\RivianHackatonEngine\CVEngine
py -3.13 -m venv venv
Set-ExecutionPolicy -Scope Process Bypass        # allows the next line in this window only
.\venv\Scripts\Activate.ps1                       # the prompt now starts with (venv)
python -m pip install -r requirements-demo.txt
python -m pip install --no-deps -r requirements-hands.txt
```
`requirements-hands.txt` is MediaPipe (Google's face and hand models). It is installed with `--no-deps` on purpose:
otherwise it brings a second OpenCV package that breaks the first one.

**Optional, for the fast setup (recommended on Intel CPUs):**
```powershell
python export_openvino.py
```
This downloads the YOLO models and converts them once into OpenVINO format (folders `*_openvino_model`, ~1 min).

macOS / Linux: `python3 -m venv venv`, `source venv/bin/activate`, and use `/` instead of `\` in paths.

Every new terminal: `cd <repo>\RivianHackatonEngine\CVEngine` and `.\venv\Scripts\Activate.ps1` again.

## 3. Run the demo videos

The demo videos are in `RivianHackatonEngine\CVEngine\use_this_videos\`. Run one command at a time from
`RivianHackatonEngine\CVEngine` (with the environment active).

**Fast setup** (after `export_openvino.py`; Intel CPU):
```powershell
python live.py "use_this_videos\ AAA.mp4" --realtime --show --no-hands --pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/ --forward-start --text-scale 0.75
python live.py "use_this_videos\AAA05_23_62_010122_video_inCabin.mp4" --realtime --show --no-hands --pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/ --forward-start --text-scale 0.75
python live.py "use_this_videos\AAA08_14_62_084339_video_inCabin.mp4" --realtime --show --no-hands --pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/ --forward-start --text-scale 0.75
python live.py "use_this_videos\AAA08_14_62_092916_video_inCabin.mp4" --realtime --show --no-hands --pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/ --forward-start --text-scale 0.75
```

**Standard setup** (any computer, no export needed; slower on CPU):
```powershell
python live.py "use_this_videos\ AAA.mp4" --realtime --show --no-hands --pose-model yolo11s-pose.pt --forward-start --text-scale 0.75
python live.py "use_this_videos\AAA05_23_62_010122_video_inCabin.mp4" --realtime --show --no-hands --pose-model yolo11s-pose.pt --forward-start --text-scale 0.75
python live.py "use_this_videos\AAA08_14_62_084339_video_inCabin.mp4" --realtime --show --no-hands --pose-model yolo11s-pose.pt --forward-start --text-scale 0.75
python live.py "use_this_videos\AAA08_14_62_092916_video_inCabin.mp4" --realtime --show --no-hands --pose-model yolo11s-pose.pt --forward-start --text-scale 0.75
```
Note: the first file name starts with a space (`" AAA.mp4"`); keep the quotes exactly as written.
A window opens after ~10 s of model loading and plays the video at its real speed. Press **q** in the window to stop.

## 4. What you see

**Window** (drawn on each analyzed frame):
- white skeleton: the driver's body points; colored dots on the head: head direction
- phone boxes: **red** = counted as phone use, **magenta** = a phone is seen but not used (e.g. lying in the tray)
- status lines: head (forward / turned / down, in degrees), eyes (open / closed), posture, people in the car,
  phone evidence, yawn, and **`FLAGS 0x..`**: the byte sent to the alert and scoring engines every frame

**The flags byte** (one bit per behaviour; several can be on at once):

| Bit | Behaviour | How it is detected |
|---|---|---|
| `0x80` | asleep | eyes closed (face model) or slumped posture (body points); not while the driver is using a phone |
| `0x40` | eyes off the road | head turned >= 20 deg (or down / up) from the driver's own forward pose |
| `0x20` | hands off the wheel | **off in this demo** (`--no-hands`): the car has a steering-wheel sensor for this |
| `0x10` | phone use | phone at the ear, or in the hand and moving / looked at; a phone lying still is ignored |
| `0x08` | yawn | mouth opens wide, then a hand covers it |

Example: `FLAGS 0x50` = eyes off the road (0x40) + phone use (0x10).

**Terminal** at the end (or after q):
```
----------------------------------------------------------------
 Video      08_14_62_084339_video_inCabin.mp4
 Analyzed   181 frames of 30.0 s video in 30.3 s, 719 skipped to stay live
 Speed      167 ms per frame = 6.0 frames per second (95% under 201 ms)
 Detected   EYES_OFF_ROAD                   3.6-5.2 s, 8.1-30.0 s
            PHONE_USE                       16.4-28.4 s
----------------------------------------------------------------
```
- **Analyzed / skipped**: the video runs at its real 30 frames per second; the analysis always takes the newest frame
  and skips the ones that arrive while it is busy, exactly like a live camera. It never falls behind.
- **Speed**: analyzed frames per second on this computer. More is better (faster reactions, short events caught).
- **Detected**: when each flag was on, in video time. (The alert engines then decide how long a behaviour must last
  before the driver is warned; that part is not in this demo.)

## 5. The options in the command

| Option | Meaning |
|---|---|
| `--realtime` | play the video at its real speed, like a camera (without it, every frame is analyzed: slow motion) |
| `--show` | open the window with the drawings |
| `--no-hands` | skip hands-off-wheel detection. **Recommended**: the car has a steering-wheel sensor, and skipping it saves a large model, ~30 % time and ~2 s at start-up |
| `--forward-start` | learn the driver's "looking at the road" head pose from the first 4 s (the clips start with the driver looking ahead); nothing is reported in the first 2 s |
| `--text-scale 0.75` | size of the text in the window |
| `--pose-model` / `--phone-model` | which AI models to use (next section) |
| `--verbose` | show the detailed technical log instead of the short summary |
| `--no-phone`, `--no-yawn`, `--no-sleep-bit` | switch single detections off |

Camera instead of a video: `python live.py 0 --show --no-hands` (camera index 0). The detections are tuned for the
car's cabin camera (driver on the right, seen from above), so a laptop webcam only roughly works.

## 6. The AI models

| Model | Job | Used |
|---|---|---|
| YOLO11 pose (Ultralytics) | finds people and 17 body points (nose, eyes, shoulders, elbows, wrists) | every frame |
| YOLO11m detection (Ultralytics) | finds the phone ("cell phone") in the driver area | every frame |
| MediaPipe Face Landmarker (Google) | 478 face points: head angles, eye closure, mouth opening | every frame |
| MediaPipe Hand Landmarker (Google) | 21 points per hand: fingers on the phone, hand over the mouth | only when needed |
| fine-tuned phone model (ours, optional) | phones in **dark** frames, trained on 1,400 labeled frames | dark frames, only if `phone_dataset\runs\phone_ft\weights\best.pt` is present |

All models download automatically on first use, except our fine-tuned one (not in the repository; without it
phones in dark clips are found less often; the bright demo clips are not affected).

**Model choices and speed** (measured on an 8-core Intel Xeon, no GPU):

| Setup | Pose model | Phone model | Frames per second |
|---|---|---|---|
| Standard (largest, default) | `yolo11m-pose.pt` (default if `--pose-model` is left out) | `yolo11m.pt` (default) | ~3.5 |
| Standard, small pose | `yolo11s-pose.pt` | `yolo11m.pt` | ~4.5 |
| **Fast (Intel OpenVINO)** | `yolo11s-pose_openvino_model/` | `yolo11m_dyn_openvino_model/` | **~5-6** |

- The **pose** model can be the small one (`s`): same results on our clips.
- The **phone** model must stay the medium one (`m`) at full size: smaller phone models miss phones held in the hand.
- **OpenVINO** is Intel's free engine for running AI models fast on Intel processors. It runs the **same** models
  (same results: phone detection identical on 114 of 116 clips), just ~1.8x faster on Intel CPUs.
  On other processors (AMD, Apple M-series) it may install and run, but we have not measured it; if
  `pip install openvino` or `export_openvino.py` fails, or it is not faster, use the standard setup (`.pt` models).
- With an **NVIDIA GPU** and a CUDA build of PyTorch, the standard `.pt` models run on the GPU automatically and are
  much faster than any CPU setup.
- Speed depends on the computer and on what else is running (on our shared test machine the same run varied between 4.6 and 6.2 frames per second). Run only one demo at a time: two runs share the CPU and both get slower.

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `can't open file ...live.py` | run from `RivianHackatonEngine\CVEngine` |
| `No module named cv2 / ultralytics / mediapipe` | activate the environment (`.\venv\Scripts\Activate.ps1`) or redo step 2 |
| `Activate.ps1 cannot be loaded` | run `Set-ExecutionPolicy -Scope Process Bypass` first |
| `...openvino_model... does not exist` | run `python export_openvino.py` once, or use the standard setup |
| video in slow motion | add `--realtime` |
| much slower than expected | another heavy program or a second demo is running |
| first start is slow | models are being downloaded (only the first time) |

More detail: [RUN_LIVE.md](RUN_LIVE.md) (all options), [BENCHMARKS.md](BENCHMARKS.md) (measurements),
[ALGORITHM.md](ALGORITHM.md) (how the head / eyes / posture detection works).
