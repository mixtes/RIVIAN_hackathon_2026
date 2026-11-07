# CVEngine: how the algorithm works

Status as of 2026-11-07. Written from the code in this folder (`live.py`, `auto_wheel.py`, `hands_*.py`,
`eyes_off_road.py`, `head_face.py`, `eyes_closed.py`, `cv_flags.py`, `rules.py`).

## 1. Big picture

A video, or a camera, is read frame by frame. Every Nth frame (`--stride 4`) goes through a fixed chain of models and
rules. Nothing looks at future frames. The result is one record per analyzed frame, `timestamp,flags,occupants` (the flags byte and the number of people in the car),
where each bit is a state.

```
frame ──► wheel calibration (first 8 frames only, then fixed)
      └─► person/pose model ──► driver keypoints
                │
                ├─► hands logic ──► near/away/unknown per hand ─► hands-off bit (0x20)
                ├─► face model on a head crop ─► yaw/pitch/blink ─► head logic ─► off-road bit (0x40)
                ├─► occupant counter (all detected people, zones) ─► occupants field
                └─► eye-closure logic (new, overlay/CSV only) ─► 0x80 (on by default; --no-sleep-bit turns it off)
```

## 2. Models: what each does, and whether it was fine-tuned

**No model was fine-tuned or trained by us.** All four are pretrained and used as they come. The only things fitted on
our own data are numbers: thresholds and one small linear regression (section 5).

| Model | File | Used for | Where |
|---|---|---|---|
| YOLO11m-pose | `yolo11m-pose.pt` | Finds people and 17 body keypoints (nose, eyes, ears, shoulders, elbows, wrists, hips). Runs on every analyzed frame on the whole frame at 640 px, on the Mac GPU (MPS). The driver is the largest person on the right half of the image. | `live.py` |
| YOLOE-11m-seg | `yoloe-11m-seg.pt` + `mobileclip_blt.ts` | Open-vocabulary segmentation: it gets the text prompts "steering wheel" and "car steering wheel" and returns a mask. `mobileclip` is the text encoder that turns the prompts into embeddings. Used only for the wheel calibration. | `auto_wheel.py` |
| MediaPipe HandLandmarker | `hand_landmarker.task` | 21 landmarks per hand. Run on a 400 px window around where the pose model thinks each hand is, because a hand is too small for it in the full wide-angle frame. | `hands_mediapipe.py` |
| MediaPipe FaceLandmarker | `face_landmarker.task` | Run on a square crop around the head (placed from the pose nose and eye distance). Outputs the head rotation matrix (turned into yaw, pitch, roll in degrees), 52 blendshape scores (including eye blink and eye gaze) and landmarks. About 9 ms per call. | `head_face.py` |

`yolo11m.pt` (plain detection) belongs to the earlier phone-detection pipeline; the live loop does not use it.

## 3. Wheel calibration (`auto_wheel.py`)

1. The wheel is assumed to be in the bottom-right 55% of the frame, so only that crop is segmented.
2. For the first 8 analyzed frames YOLOE proposes masks. A mask is kept only if its geometry is plausible: size,
   centroid position, a box not too large, and solidity.
3. The masks from the different frames are voted: a pixel counts if a strict majority of the reliable masks cover it.
   An ellipse is fitted to the voted shape, which removes bumps from arms and hands.
4. The fitted ellipse is checked against a prior ellipse (the median of three verified clips from this camera). If the
   detection disagrees with it or is not ellipse-like, the system falls back to the prior ellipse and labels it
   "prior, not detected".
5. A wrist tolerance margin of 15% of the shorter wheel dimension is added.

After this the wheel is fixed for the whole video. It costs about 3 s of startup.

## 4. Hands-off-wheel logic (bit 0x20)

For each hand, per frame:

- **Pose proxy** (`hands_pose.py`): the hand centre is estimated as the wrist pushed 30% further along the forearm
  (wrist minus elbow). Inside the wheel zone plus margin it is "near", outside it is "away", and with no reliable wrist
  it is "unknown".
- **MediaPipe hand** (`hands_mediapipe.py`): "near" if at least 3 of the 21 landmarks are on the wheel (12 px margin),
  "away" if the hand is found but fewer are, "unknown" if no hand is found.
- **Hybrid** (`hands_hybrid.py`, the default): if MediaPipe found the hand, trust it. If it found nothing, a pose "near"
  stays near, and a pose "away" becomes **unknown**. This is deliberate: the pose proxy's "away" is its unreliable
  direction (a hand gripping the outer rim can look away), and "unknown" can never raise an alert.

The frame label combines both hands: near if either hand is near, off if both are away, otherwise unknown. Then:

- `HandsOffMachine` in `live.py` produces the confirmed/ended events: both hands away for 1.0 s confirms, a hand near
  for 0.5 s ends it, 2 s without usable observations resets it.
- The byte uses a separate symmetric flicker filter (0.3 s). The engines own the duration thresholds. Unknown for more
  than 0.5 s forces the bit to 0.

`rules.py` is the older offline pipeline (cached keypoints, phone-at-ear, hand-in-lap rules). The live path reuses only
a few helpers from it (`on_wheel`, `HAND_EXT`, `KP_CONF`).

## 5. Eyes-off-road logic (bit 0x40), in `eyes_off_road.py`

- **Source:** if the face model finds a face (81 to 100% of frames) its yaw and pitch in degrees are used. Otherwise a
  pose proxy is used: yaw = (nose x - eye midpoint x) / eye distance. The two sources have separate baselines and
  thresholds.
- **Rule:**
  - turned when yaw differs from the baseline by 20 deg or more (leaves below 14 deg)
  - down when pitch is 12 deg or more above the baseline (leaves below 8 deg)
  - up when pitch is 15 deg or more below the baseline (leaves below 10 deg)
  - pitch is only evaluated while the head is not turned
  - no face and no keypoints gives "unknown", and the bit stays 0
- **Why a baseline:** the camera sees the driver from the side, so "looking at the road" is not 0 deg. Over the 50 clips,
  forward yaw ranged from -35 to +51 deg.
- **Calibration, in three parts:**
  1. **Position prior.** A linear regression predicts the forward angle from the head's position in the frame (nose x,
     nose y, eye distance). Fitted on 42 of the 50 clips; leave-one-clip-out error about 7.7 deg yaw and 5.4 deg
     pitch. **This regression is the only thing fitted on our data.**
  2. **Warm-up.** The first 4 s only collect data; the bit stays 0.
  3. **Learning.** The baseline is the most frequent head pose over about the last 2 minutes (a histogram mode, so
     glances do not pull it). Samples are weighted by a Gaussian around the prior, and the prior counts as 4 s of data.
- **`--forward-start` mode (live.py).** For clips that begin with the driver on the road: no prior, no 4 s warm-up,
  no long learning. Nothing is reported for the first 2 s; the baseline is the most frequent pose of the first 4 s
  (a mode, so a glance at the camera at the start is tolerated), then it is frozen (no drift).
  Weakness: a pose held for most of the first 4 s is taken as forward, so a real off-road look right at the start
  is absorbed.
- **Head not visible (state `away`, counts as off road).** If the head cannot be measured for 1 s while the driver
  is still seen (pose keypoints with a weak head, or the occupancy counter sees someone in the driver zone although
  the pose model gave no driver keypoints), the state is `away` and bit 0x40 is set. It ends on a forward
  observation, or after 2 s without any sign of the driver. Covers the driver bent down or fully turned away.

## 6. Output byte and live loop (`cv_flags.py`, `live.py`)

| Bit | Meaning | Status |
|---|---|---|
| 0x80 | eyes closed / asleep / slumped | implemented (eyes_closed.py + slump.py), on by default, own `FlickerFilter` |
| 0x40 | looked off the road | implemented |
| 0x20 | hands off the wheel | implemented |
| 0x10 | phone distraction | not wired |
| 0x0F | reserved | always 0 |

Each of 0x40 and 0x20 goes through its own `FlickerFilter` (symmetric 0.3 s; unknown for more than 0.5 s gives 0).
Speed is about 6.4 to 8 fps (130 to 157 ms per frame), so at 30 fps source, stride 4 is borderline.

## 6b. Occupant count (`occupancy.py`, new)

How many people are in the car, front and rear seats, as the third field of the record (`timestamp,flags,occupants`).
It uses the person detections the pose model already produces, so it costs no extra model run.

1. **Filter.** Keep a detection if its confidence is at least 0.25 (the driver 0.5), at least 5 keypoints are visible
   with a head point among them, and the box is at least 12% of the frame height.
2. **Dedupe.** The pose model often draws several boxes around one person. Two detections are the same person when
   their head points are close (relative to the narrower box), or one box lies mostly inside the other and the heads
   are still near. The narrower box is the reference on purpose: a huge driver box must not swallow a rear passenger.
3. **Zones.** Driver: the largest person on the driver's side. Front passenger: any other person whose box is tall or
   reaches the bottom of the frame. Rear: every other person (small box, high in the frame). The front/rear split is a
   geometric rule that has not been checked with a real front passenger present.
4. **Smoothing.** Per zone, over the last 3 s: the count rises to k when at least half of the recent frames saw k
   people, and falls below k only when fewer than a quarter did. A person who is missed for a moment does not make the
   count flicker, and a one-frame ghost does not add a person.

The count is a lower bound: people hidden by a seat or another person, lying down, or outside the camera view are not
counted. It is informational and never raises an alert.

## 7. Eye-closed detector (`eyes_closed.py`, new)

Uses only the face model's blink score. It has:

- a per-driver open-eye reference (30th percentile of the last 60 s of open-eye scores)
- a gaze-down discount, so looking down is not read as closing
- hysteresis, and a 0.5 s blink filter (shorter closures are ignored)
- a 0.3 s release, and "unknown" after 0.5 s without a face

It feeds the overlay and the CSV (`eye_*` columns). The sleep thresholds are meant to live in the downstream engines.

## 8. Known problems and edge cases

- **Glasses.** Reflections and glare, the frame covering the lid line, and dark lenses can break the blink score. The
  detector is not reliable for drivers with glasses, and a different approach is needed (see section 9).
- **Reversing.** With the head turned all the way back, the face disappears, the state becomes "unknown" and the bit
  drops to 0 at the moment the head is turned furthest. Suggestions: latch the "turned far" state when the face is lost
  right after a turn; gate the eye detector when the head is turned more than about 40 deg; use the downstream gear
  signal to suppress the alert in reverse (needs `requested_changes.md`); optionally a reserved bit for "turned far".
- **Head-down detection is weak.** The look-down at 8.07 s in 011124 was not flagged.
- **Start of drive.** A driver who does not look at the road during the first ~15 s gets a wrong baseline.
- **No ground-truth labels.** Thresholds, the position prior and the baseline quality are all in-sample (about 50 clips,
  one camera). "Up" has hardly any validation.
- **Pose fallback** is the least calibrated head source.
- **iCloud.** Files on `~/Desktop` can be evicted ("dataless"), which stalls runs for minutes. Use Finder
  "Keep Downloaded" on the project and venv folders, or move the project out of Desktop.

## 9. Ideas for eyes closed when the driver wears glasses

To be decided; none of these is built.

1. Detect glasses (face landmarks, or a stable low blink-score variance) and report the eyes as "not observable" (bit 0,
   never a false alarm) for that driver.
2. Use signals that do not depend on seeing the eye: head drop (large sustained pitch down), no head or hand movement for
   a long time, and the combination of bits 0x40 and 0x20, which the downstream engines can read as incapacitation.
3. Measure the eyelid region directly (eye aspect ratio from landmarks, or the vertical lid gap), with glare handling.
   Needs testing on glasses clips.
4. A near-infrared camera would remove most of the glare problem (a hardware question).


## 10. Sleep / health detection, second version (eyes_closed.py + slump.py)
Found on the real collapse clip: when a driver faints, the face disappears (head down, turned into the seat), so every
face cue goes "unknown" and the first version could never fire. And the blendshape blink score alone is noisy.
1. Eyes: the blink score is fused with the eye aspect ratio (EAR) of the eyelid landmarks, both normalised to the
   driver's own open-eye level. A frame is closed only when they agree; looking down is discounted. Blink filter
   0.5 s as before. New fatigue measures: PERCLOS (share of the last 60 s with closed eyes, drowsy at 15%) and the
   number of long closures in the last 120 s (drowsy at 3). They go to the CSV/overlay (`perclos`, `microsleeps`,
   `drowsy`), not into the byte.
2. Posture (`slump.py`): ratio = (shoulder_y - head_y) / shoulder_width from the pose keypoints. Upright is about +0.55,
   a collapse drops it to below 0.25 (negative when the head is below the shoulders). Per-driver baseline, hysteresis,
   confirmed after 0.6 s. A slumped driver is often lost by the person detector: for 2.5 s after the last slumped
   observation a missing driver still counts as slumped, then "unknown" (bit 0).
3. Bit 0x80 = eyes closed OR slumped, through a `FlickerFilter`. Alert durations stay with the downstream engines.
