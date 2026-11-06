# AlertEngine: the interface the car app expects

> For: the engine team (owners of `RivianHackatonEngine/`).
> Written: 2026-11-06, agreed with the car-app owner.
> Companion doc: [`../GameEngine/CAR_FRONTEND_INTERFACE.md`](../GameEngine/CAR_FRONTEND_INTERFACE.md).
> Wire protocol source of truth: [`car_frontend/frontend_specification.md`](../../car_frontend/frontend_specification.md) §8 and §9.

If you build exactly what's in this document, the car app will work with it unchanged (apart from the planned *dismiss* addition, marked **[planned]**, which we build on our side).

---

## 1. The big picture

```
 detection (CVEngine, simulator, ...)
        │  on_event("PHONE_CALL", is_active=True, ts)
        ▼
 ┌──────────── RivianHackatonEngine (YOU: pure Python, no networking) ───────────┐
 │  SafetySystem (main.py)                                                       │
 │    ├─ AlertEngine ──▶ Alert / AlertClear objects (returned from each call)    │
 │    └─ GameEngine  ──▶ DriveReport at end_drive()                              │
 └───────────────────────────────────────────────┬───────────────────────────────┘
                                                 │ imported as a library
 ┌──────────── simulated_game_engine/ (US: the bridge) ──────────────────────────┐
 │  WebSocket server ws://…:8765/ws, sessions, acks, wall-clock times, keep-alive │
 └───────────────────────────────────────────────┬───────────────────────────────┘
                                                 ▼
                                   Car app (Android Automotive)
```

**Decision: the AlertEngine is a pure Python library.** It doesn't open sockets, start threads, sleep, read the wall clock or print. The bridge (`simulated_game_engine/`) imports it, calls it, and turns its return values into WebSocket messages for the car app.

What this means for the current `AlertEngine.py` (commit `96eb14b`):
- Its **pure `AlertEngine` class** is the right idea. Keep that style: every call returns a list of what changed.
- Its **WebSocket server** (`serve()`, `ALARM_START`/`ALARM_END`/`SNAPSHOT`/`DISMISS`) is not used by the car app. Delete it, or keep it as a dev-only tool under `if __name__ == "__main__":`. If you keep it, it **must not use port 8765** (the bridge's port). Use 8766 instead. Importing the module must never start a server.
- `main.py` currently imports `Alert` and `AlertClear`, which `AlertEngine.py` no longer defines. That breaks the bridge on `main` today (`ImportError`). This document defines both classes again (§4).

---

## 2. Hard requirements (checklist)

1. **Pure and deterministic.** All timing comes from the `timestamp` argument (float seconds, monotonic). `timestamp=None` means `time.monotonic()`. The same inputs must always give the same outputs.
2. **Stdlib only at import time.** Importing `AlertEngine.AlertEngine` must not need `websockets`, `numpy` or similar packages.
3. **Python 3.9 to 3.12.** The bridge runs 3.12; CVEngine runs 3.9. Use `from __future__ import annotations` and `typing.List`/`Optional` (no `match` and no runtime `X | Y`).
4. **Keep the import paths.** The bridge puts `RivianHackatonEngine/` on `sys.path` and loads `main.py`. Keep `AlertEngine/AlertEngine.py`, `GameEngine/GameEngine.py` and `SafetySystem` in `main.py`.
5. **All 8 canonical actions exist** (§3), including the ones nobody detects yet (stubs).
6. **Not thread-safe, and that's fine.** The bridge calls the engine from one thread only.
7. **Never raise on bad input from detection.** Unknown action → return `[]`. Unknown `alert_id` in `dismiss` → return `[]`.

---

## 3. Actions: 8 canonical types, stubs for the rest

These 8 keys are shared by the engine, the bridge, the car app and the backend. They are fixed: don't add, rename or drop any.

| Key | Meaning | Likely detected for the demo? |
|---|---|---|
| `PHONE_CALL` | phone at the ear (CVEngine `PHONE_AT_EAR`) | yes |
| `PHONE_USE` | phone in hand / looking at it (CVEngine `PHONE_IN_HAND`, `HEAD_DOWN_HAND_IN_LAP`) | yes |
| `HANDS_OFF_WHEEL` | no hand on the wheel (CVEngine `HANDS_OFF_WHEEL`) | yes |
| `EYES_CLOSED` | eyes closed / **drowsiness** (this is where your latched drowsiness alarm goes) | maybe |
| `EYES_OFF_ROAD` | looking away from the road | stub |
| `YAWN` | yawning | stub |
| `EATING_DRINKING` | eating or drinking | stub |
| `SMOKING` | smoking | stub |

- **There is no `DROWSINESS` type.** Drowsiness is reported as `EYES_CLOSED`. That way it is scored, shown on the Details screen and uploaded to the backend without any backend change.
- CVEngine labels that aren't in the list (e.g. `ONE_HAND_ON_WHEEL`) must be mapped to one of the 8 or dropped **before** they reach `on_event`. That mapping is your call.
- **Stubs:** every one of the 8 has an entry in `DEFAULT_ALERT_CONFIG` with sensible default steps (the old config from before `96eb14b` is a good starting point), and a `# STUB: not detected yet` comment. A stub is still fully working: the simulator can trigger it in a demo. It's just that no real detector sends it yet.
- Also add `DETECTED_ACTIONS` to `main.py` (§6): the subset that real detection actually produces. The bridge reports it to the app as debug info.
- Keys are **UPPER_CASE**. Normalize every input with `action.strip().upper()`, so `"phone_call"` works too.

---

## 4. Types

```python
from enum import IntEnum
from dataclasses import dataclass
from typing import List, Optional, Union

class AlertLevel(IntEnum):
    NONE = 0       # internal only: never put in an Alert
    VISUAL = 1
    AUDIO = 2
    HAPTIC = 3
    CRITICAL = 4

@dataclass
class Alert:
    alert_id: str            # str(uuid.uuid4()); same id for every escalation of one occurrence
    action: str              # one of the 8 keys, UPPER_CASE
    level: AlertLevel        # VISUAL..CRITICAL, never NONE
    message: str             # non-empty; shown on screen AND spoken by text-to-speech
    duration_so_far: float   # seconds the action has lasted when this alert is raised (>= 0)
    timestamp: float         # monotonic seconds; occurrence start = timestamp - duration_so_far
    requires_ack: bool = False   # True = latched: stays until the driver presses Dismiss

@dataclass
class AlertClear:
    alert_id: str            # the id of the Alert being removed
    action: str
    last_level: AlertLevel   # highest level shown in this occurrence
    duration: float          # how long the occurrence lasted (seconds)
    timestamp: float
    reason: str              # "ended" | "dismissed"

AlertUpdate = Union[Alert, AlertClear]

@dataclass
class EscalationStep:
    after_seconds: float     # time since the occurrence started
    level: AlertLevel
    message: str

@dataclass
class ActionAlertConfig:
    steps: List[EscalationStep]      # sorted by after_seconds; levels strictly increasing
    latched: bool = False            # True = alerts need a driver Dismiss (requires_ack)
```

Use these exact class and field names: the bridge reads them by name. You may add extra fields with defaults; the bridge ignores them. `AlertLevel` names are sent to the app as strings (`"AUDIO"`), so don't rename them.

---

## 5. `AlertEngine` API

```python
class AlertEngine:
    config: Dict[str, ActionAlertConfig]          # keyed by UPPER_CASE action, all 8 present

    def __init__(self, config: Optional[Dict[str, ActionAlertConfig]] = None): ...

    def process_event(self, action: str, is_active: bool,
                      timestamp: Optional[float] = None) -> List[AlertUpdate]: ...
    def tick_all(self, timestamp: Optional[float] = None) -> List[AlertUpdate]: ...
    def dismiss(self, alert_id: str, timestamp: Optional[float] = None) -> List[AlertUpdate]: ...

    def active_alerts(self) -> List[Alert]: ...   # latest Alert of every alert still showing
    def active_actions(self) -> List[str]: ...    # actions currently is_active=True
    def reset(self) -> None: ...                  # forget everything, emit nothing
```

**Everything is returned. There are no callbacks.** Drop the old `on_clear` callback: return `AlertClear` objects from the call that caused them. The bridge sends the list in order, so put an `AlertClear` before a new `Alert` when both happen in one call.

### 5.1 Behavior rules

**Occurrences and ids**
1. An **occurrence** starts with the first `is_active=True` for an action and ends with `is_active=False`. A latched occurrence ends at `dismiss` instead (rule 8).
2. Each occurrence gets **one new `alert_id`** (`uuid4`). Every escalation of that occurrence reuses it. The app updates the same banner in place, and plays the new level's sound.
3. Repeated `is_active=True` while active is **idempotent**: it doesn't restart the timer or change the id. Detectors may send it every frame.

**Escalation**

4. `process_event(..., True)` and `tick_all()` return an `Alert` **only when the level goes up** (including the first step). The level never goes down within an occurrence. The bridge calls `tick_all()` about every 100 ms. It must return the escalations of **all** actions (don't drop any; order doesn't matter).
5. An occurrence that ends before its first step doesn't produce an `Alert`, so it produces no `AlertClear` either.

**Clearing**

6. `process_event(..., False)` on a **non-latched** action that raised an alert → `AlertClear(reason="ended")`.
7. **Don't suppress alerts because another one has priority** (e.g. "drowsiness silences hands-off-wheel"). Report every alert. The app shows one at a time by level (`CRITICAL > HAPTIC > AUDIO > VISUAL`), preempts lower ones, and resumes them if they are still active.

**Latched alerts (drowsiness) [planned in the app]**

8. For an action with `latched=True`, every `Alert` has `requires_ack=True`. When the action ends (`is_active=False`), the alert **stays**: no clear, and the level stays where it is. It only goes away through `dismiss(alert_id)`, which returns `AlertClear(reason="dismissed")`.
9. If the action is **still active** at `dismiss` time, or starts again later, that's a **new occurrence** (new `alert_id`), timed from that moment. This is your "rings again right after dismiss" rule.
10. `dismiss()` on a non-latched alert, or on an unknown or already-cleared id → `[]`.

**Session**

11. `reset()` is called when a drive starts and ends. It clears all state without returning anything; the bridge itself sends clears for every alert it has forwarded.

### 5.2 Your current TIMED / LATCHED rules in this model

| `96eb14b` idea | In this interface |
|---|---|
| `drowsiness` LATCHED, rings until dismissed | `EYES_CLOSED` with `latched=True`, e.g. one step `0.5 s → CRITICAL "Wake up! Pull over and rest."` |
| `drowsiness` has priority over hands | Nothing to do: the app shows CRITICAL above everything else |
| `hands_off_wheel` TIMED: 3 s on, 1 s pause, repeat | Express it as escalation instead: e.g. `1 s VISUAL → 3 s AUDIO → 6 s HAPTIC → 10 s CRITICAL`. The app repeats the CRITICAL tone every 2 s by itself |
| `ALARM_START` | `Alert` |
| `ALARM_END` (`timeout` / `dismissed` / `preempted`) | `AlertClear` (`ended` / `dismissed`). There's no `timeout` or `preempted` any more |
| `DISMISS {alarm_id}` | `dismiss(alert_id, ts)` (the bridge calls it when the app sends `alert.dismiss`) |
| `SNAPSHOT` on connect | `active_alerts()` (the bridge uses it on reconnect and for its keep-alive) |

---

## 6. Your part of `SafetySystem` (`main.py`)

```python
__version__ = "0.3.0"          # short (the bridge prefixes it and the backend caps it at 40 chars)

DETECTED_ACTIONS = ("PHONE_CALL", "PHONE_USE", "HANDS_OFF_WHEEL")   # what real detection emits

class SafetySystem:
    def __init__(self): ...                     # no required arguments
    alerts: AlertEngine
    game: GameEngine

    def on_event(self, action, is_active, timestamp=None) -> List[AlertUpdate]:
        # feeds BOTH engines; returns the AlertEngine's updates
    def tick_all(self, timestamp=None) -> List[AlertUpdate]:
    def dismiss(self, alert_id, timestamp=None) -> List[AlertUpdate]:
        # AlertEngine only. A dismiss never changes the score.
    # start_drive / end_drive: see the GameEngine doc. Both also call self.alerts.reset().
```

Remove `tick()` (the "most severe only" version) and the `on_clear` argument. The bridge will stop using them.

---

## 7. What happens downstream (so you can choose levels and texts)

**How the bridge maps your objects to wire messages** (frontend spec §8.3):

| Your object | Wire message to the app |
|---|---|
| `Alert` | `alert {alert_id, session_id, type: action, level: level.name, message, duration_so_far_s, started_at (wall clock), ttl_s, requires_ack}` |
| `AlertClear` | `alert.clear {alert_id}` |
| app sends `alert.dismiss {alert_id}` **[planned]** | `system.dismiss(alert_id, now)` |

**What the driver experiences per level** (frontend spec §9.1):

| Level | Screen | Sound | Voice (TTS) | App drops it after (no update) |
|---|---|---|---|---|
| `VISUAL` | amber top banner | none | no | 8 s |
| `AUDIO` | amber top banner | short chime | yes, once | 8 s |
| `HAPTIC` | red pulsing banner (the car has no vibration API, so this is visual + sound) | double chime | yes, once | 10 s |
| `CRITICAL` | full-screen red pulsing overlay | alarm tone every 2 s | repeated every 6 s | 15 s |
| `CRITICAL` + `requires_ack` **[planned]** | same, plus a big **Dismiss** button | same | same | never (stays until Dismiss) |

- **Keep-alive is the bridge's job.** While an action stays active, the bridge re-sends your latest `Alert` every 5 s, so the app doesn't drop it. Don't re-emit the same level yourself.
- The app doesn't replay the chime/TTS for the same type at the same level within 10 s of a clear (anti-nagging). You can add your own cooldowns on top.
- **Messages:** the app shows and speaks your `message` exactly as you send it. Keep it short (under about 40 characters), in English and in plain words, with no emoji. The app only uses its own fallback text if `message` is empty.

---

## 8. Acceptance tests (please add them as unit tests)

All timestamps are in seconds, with no real clock.

1. `process_event("phone_call", True, 0.0)` → an `Alert` with `action == "PHONE_CALL"` and `level` = the first step's level (if that step is at 0 s).
2. Holding an action active with `tick_all` every 0.1 s produces exactly one `Alert` per configured step, all with the **same** `alert_id`, in increasing level.
3. Two actions escalating in the same `tick_all` → **both** alerts are returned.
4. `process_event(X, False)` after an alert → exactly one `AlertClear(reason="ended")` with the same `alert_id`. A second `False` → `[]`.
5. End then start again → a **different** `alert_id`.
6. A latched `EYES_CLOSED`: after `False`, there's no clear and `active_alerts()` still contains it. `dismiss(id)` → `AlertClear(reason="dismissed")`. `dismiss(id)` again → `[]`.
7. Dismiss while `EYES_CLOSED` is still active → a clear, then a new occurrence with a new id.
8. `process_event("NOT_A_THING", True, 0.0)` → `[]`, with no exception.
9. All 8 actions are in `AlertEngine().config`, and `DETECTED_ACTIONS` ⊆ those 8.
10. `import main` works in a clean Python 3.9 venv with nothing installed.

When it's merged, tell the car-app owner. We'll then migrate the bridge (`simulated_game_engine/`) and add the Dismiss button to the app.
