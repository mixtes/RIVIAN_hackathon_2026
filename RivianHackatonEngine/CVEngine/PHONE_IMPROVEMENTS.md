# PHONE_USE: known limits and improvement ideas

Status (all 116 clips, stride 8): 0 false-alarm clips; real use detected in 11 clips. Logic:
[phone_events.py](phone_events.py), live wiring: `live_phone.PhoneMonitor` in [live.py](live.py) (bit `0x10`).

## Constraints (measured)

| # | Limit | Effect | Where |
|---|---|---|---|
| 1 | Detector misses held phones (bright in-hand: 084339 found 19/36 frames; dark tray phones dominate training data) | Gaps in evidence; 6 clips fully missed (011802, 015556, 025248, 025453, 030915, 085155) | `yolo11m.pt`, `phone_ft/best.pt` |
| 2 | Fine-tuned dark model treats a dark tray as a phone (conf 0.88-0.90) | Phone "seen" every frame; rules must reject it | `best.pt` |
| 3 | Start confirmation ~1 s (3 positives, >= 60 %, >= 1 s span in 2 s) | First alert ~1 s late; loosening brings back the 010829 false alarm | `EventTracker` |
| 4 | End after 3 s without positives | Bit stays on up to ~3 s after the phone is put down; 2 s splits use (12 -> 15 alerts) | `END_GAP_S` |
| 5 | MediaPipe rarely finds the hand that holds a phone (11/36 frames in 084339) | "Fingers on phone" rarely available; fallback is pose geometry (in-palm) | `hands.py` |
| 6 | Pose wrist only, no fingers; held vs lying phone separated by motion, head pitch and forearm geometry | Still phone with head up = not counted (e.g. 084339 at 20 s) | `PhoneEvidence.update` |
| 7 | Thresholds tuned on this camera (fixed crop x>=760, y>=600, driver on right, shoulder-width scale) | New camera position needs re-tuning | `live_phone.py`, constants |
| 8 | CPU speed: 281 ms/frame with `--no-hands` (3.5 fps), 403 ms with hands | Fewer frames per second = slower confirmation, more gaps | `live.py` |
| 9 | Short use at the clip/drive end (< ~1 s of evidence) is never confirmed | 021132 last 2 s missed | `EventTracker` |
| 10 | Validation is clip-level and partly by eye; no event-level ground truth | Delay / miss rates are estimates | - |

## Proposals

| # | Proposal | Fixes | Effort |
|---|---|---|---|
| A | Second training round: label more bright in-hand / at-ear phones, fewer tray phones; optionally 2 classes `phone_held` / `phone_lying` | 1, 2, 4, 6 (shorter end gap possible) | labeling 1-2 h + GPU run |
| B | Event-level ground truth for ~30 clips (start/end of real use) + script for false alarms/hour, misses, delay | 10, tuning of 3/4 | ~1 h labeling |
| C | Ignore detections inside a fixed "tray zone" unless moving or held (in-palm / fingers) | 2 | small |
| D | End early on strong evidence of release (phone seen lying still, hand away) instead of always waiting 3 s | 4 | small, check with replay |
| E | Merge a new event into the previous one if the gap is < ~5 s (one alert instead of a split) | 4 (allows shorter end gap) | small |
| F | GameEngine: `PHONE_USE grace_seconds` 1.0 -> 0 (CV already waits ~1 s) | 3 (double delay) | trivial, GameEngine owner |
| G | Run phone detection every frame but pose/head less often, or on GPU / smaller models | 8 | medium |
| H | Hand landmark search centered on the phone box (not only the wrist) | 5 | small, measure first |
| I | Per-vehicle calibration of crop and tray zone at startup | 7 | medium |

Tools for testing changes: feature cache + replay of all 116 clips in seconds (scripts not in the repo yet; add them if needed),
`live.py --flags x.csv` then `python main.py --cv x.csv` for the engine alerts.
