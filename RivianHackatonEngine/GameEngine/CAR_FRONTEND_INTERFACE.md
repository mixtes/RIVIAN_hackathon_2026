# GameEngine: the interface the car app expects

> For: the engine team (owners of `RivianHackatonEngine/`).
> Written: 2026-11-06, agreed with the car-app owner.
> Companion doc: [`../AlertEngine/CAR_FRONTEND_INTERFACE.md`](../AlertEngine/CAR_FRONTEND_INTERFACE.md). Its §1 (big picture) and §2 (hard requirements) apply here too.
> Wire protocol source of truth: [`car_frontend/frontend_specification.md`](../../car_frontend/frontend_specification.md) §8.5.

**Good news: the current `GameEngine.py` already meets almost all of this.** This document pins the contract down so it doesn't drift. The only gaps are stub markers (§2) and tests (§7).

---

## 1. Role

- A pure Python library (no networking, threads, wall clock or printing in the API). The bridge `simulated_game_engine/` imports it through `SafetySystem` in `main.py`.
- It is fed the **same driver-action events** as the AlertEngine, and returns **one `DriveReport` per drive**, at `end_drive()`. There is **no live score**: the car app shows the score only after the drive.
- Timestamps are monotonic float seconds passed in by the caller (`None` → `time.monotonic()`). The bridge converts them to wall-clock ISO times for the app.
- Stdlib only, Python 3.9 to 3.12, path stays `GameEngine/GameEngine.py`.

---

## 2. Actions: the same 8 canonical types, stubs for the rest

`PHONE_CALL`, `PHONE_USE`, `HANDS_OFF_WHEEL`, `EYES_CLOSED`, `EYES_OFF_ROAD`, `YAWN`, `EATING_DRINKING`, `SMOKING`.

- These 8 are the backend's and the app's mistake types. **Don't add, rename or drop any.** The backend would accept any `UPPER_SNAKE` type, but it only has labels and stats for these 8. An unknown type would show up in the app with a fallback label and wouldn't line up across drivers.
- **Drowsiness is scored as `EYES_CLOSED`.** There's no `DROWSINESS` type.
- **All 8 must be in `DEFAULT_SCORE_CONFIG`** (they are today). We'll probably only detect about 3 for real. Mark the others with `# STUB: not detected yet`, but keep them fully working, so the simulator can still trigger them in a demo.
- Normalize input with `action.strip().upper()`. Ignore unknown actions silently (like the `DRIVE_END` row in `test_inputs.csv`).

---

## 3. API (keep exactly this; it's what `GameEngine.py` already has)

```python
class GameEngine:
    config: Dict[str, ActionScoreConfig]               # UPPER_CASE keys, all 8 present

    def __init__(self, config: Optional[Dict[str, ActionScoreConfig]] = None): ...
    def start_drive(self, timestamp: Optional[float] = None) -> None: ...
    def process_event(self, action: str, is_active: bool,
                      timestamp: Optional[float] = None) -> None: ...
    def end_drive(self, timestamp: Optional[float] = None) -> DriveReport: ...
    def score_occurrence(self, occ: Occurrence) -> float: ...   # bridge uses: > 0 means "penalized"
    def run_from_csv(self, path: str) -> DriveReport: ...       # dev/test helper, optional for us
```

```python
@dataclass
class Occurrence:
    action: str            # UPPER_CASE
    start_time: float      # monotonic seconds
    end_time: float
    duration: float        # property: max(0, end_time - start_time)

@dataclass
class ActionSummary:
    action: str
    count: int             # penalized occurrences only
    total_duration: float  # sum of their durations (s)
    longest_duration: float
    penalty: float         # points lost for this action (>= 0)

@dataclass
class DriveReport:
    score: float                         # 0..100, 100 = perfect
    drive_duration: float                # seconds
    actions: Dict[str, ActionSummary]    # ALL 8 keys, even with count 0
    occurrences: List[Occurrence]        # ALL occurrences, including those under the grace period
```

The bridge reads these class and field names, so don't rename them. You may add fields with defaults; we ignore what we don't use.

### 3.1 Behavior rules

1. `start_drive(ts)` resets everything. A drive is everything between `start_drive` and `end_drive`.
2. `process_event(X, True)` opens an occurrence of X. A repeated `True` while X is open is ignored, because detectors may send it every frame. `process_event(X, False)` closes it. A `False` without an open occurrence is ignored.
3. `end_drive(ts)` closes every still-open occurrence at `ts`, computes the report and returns it. It must work even if `start_drive` was never called: return duration 0 and score 100, not an exception.
4. **Consistency (important):** for every action X, `actions[X].count` == the number of occurrences of X with `score_occurrence(occ) > 0`. `total_duration` and `longest_duration` cover exactly those occurrences. The bridge builds the per-event list from `occurrences` filtered by `score_occurrence > 0`, and the app shows both, so they must agree.
5. `score` is clamped to `[0, 100]`, and all counts, durations and penalties are `>= 0`. The app **rejects** a result outside these bounds ("Result invalid"), and that drive is lost.
6. **The score only depends on detection events**, never on alerts. Dismissing a drowsiness alarm in the app must not change the score. A latched `EYES_CLOSED` is scored by how long the eyes were actually closed (detector `True` → `False`), not by how long the alarm rang.
7. Deterministic: the same events and timestamps always give the same report.

---

## 4. Your part of `SafetySystem` (`main.py`)

```python
class SafetySystem:
    game: GameEngine

    def start_drive(self, timestamp=None) -> None:
        self.alerts.reset(); self.game.start_drive(timestamp)
    def on_event(self, action, is_active, timestamp=None):
        self.game.process_event(action, is_active, timestamp)   # always feed the GameEngine
        return self.alerts.process_event(action, is_active, timestamp)
    def end_drive(self, timestamp=None) -> DriveReport:
        self.alerts.reset(); return self.game.end_drive(timestamp)
```

The bridge calls `start_drive` when the app sends `session.start`, and `end_drive` when the app sends `session.end`, or when the bridge ends the drive itself. Also keep `__version__` (short) in `main.py`. It ends up in every result as `engine_version`.

---

## 5. What happens to your `DriveReport` downstream

The bridge maps it to the `drive.result` message (frontend spec §8.5). The app stores it locally, shows it, and uploads it to the backend unchanged.

| `drive.result` field | From your `DriveReport` |
|---|---|
| `score` | `score`, rounded to 2 decimals |
| `started_at` / `ended_at` | wall-clock times recorded by the bridge at session start / end |
| `mistakes[]` `{type, count, total_duration_s, max_duration_s}` | one per entry in `actions` (all 8): `action`, `count`, `total_duration`, `longest_duration` |
| `events[]` `{type, started_at, duration_s}` | `occurrences` where `score_occurrence(occ) > 0`, converted to wall clock |
| `extra.penalties` | `ActionSummary.penalty` per type |
| `extra.engine_drive_duration_s` | `drive_duration` |
| `engine_version` | `rhe/<__version__>+bridge-<ver>`, max 40 chars |

**What the driver sees:**
- **Result screen:** the score as a big number. In History, the score pill is **green ≥ 85**, **amber 60–84**, **red < 60**.
- **Details screen:** one row per mistake type with count, total duration and longest duration. The count pill is colored by **rate per hour of driving** (green = 0, amber < 2/h, red ≥ 2/h). The app computes this from your `count` and the drive's duration.
- **Stats and leaderboards (backend):** the average score is **weighted by driving time** (there's no distance data), and a minimum driving time is needed to qualify.

**Worth considering when you tune penalties (not a requirement):** penalties are absolute points today, so a 2-hour drive collects more of them than a 10-minute drive at the same mistake rate. That works against long drives, which is exactly what the time-weighted leaderboard gives the most weight to. Normalizing by drive time (or capping total penalty per hour) would make scores comparable across drive lengths. It's your call. Whatever you choose, it must stay within 0–100.

---

## 6. Things that must NOT change without telling the car-app owner

- The 8 action keys, and the class and field names in §3.
- The meaning of `count` (penalized occurrences only) and rule 4 (consistency).
- One report per drive, returned from `end_drive()`.

---

## 7. Acceptance tests (please add them as unit tests)

1. `GameEngine().run_from_csv("test_inputs.csv")` works. The report has all 8 keys in `actions`, and `0 <= score <= 100`.
2. An occurrence shorter than its `grace_seconds` → `count == 0` for that action, but it's still in `occurrences`, with `score_occurrence(occ) == 0`.
3. Repeated `True` at 0, 1 and 2 s, then `False` at 5 s → one occurrence of 5 s.
4. `True` with no `False`, then `end_drive(10)` → the occurrence is closed at 10.
5. Rule 4 holds for a random mix of events (property test: count == the number of penalized occurrences).
6. An enormous penalty → `score == 0`, not negative.
7. `end_drive()` without `start_drive()` → a valid report, no exception.
8. An unknown action (`"DRIVE_END"`) → ignored.
9. Running the same event list twice → identical reports.

When something here changes, tell the car-app owner, so we can update the bridge (`simulated_game_engine/`) and the app spec together.
