# AlertEngine: the interface CVEngine uses to send detections

> For: the engine team (owners of `RivianHackatonEngine/`), and the CVEngine owner.
> Written: 2026-11-06.
> Companion docs: [`../GameEngine/CV_ENGINE_INTERFACE.md`](../GameEngine/CV_ENGINE_INTERFACE.md) (same contract, scoring side), [`CAR_FRONTEND_INTERFACE.md`](CAR_FRONTEND_INTERFACE.md) (what the car app expects from this engine).
> Status: **proposal, agreed in principle with the CVEngine owner.** Sections 2 to 4 are the shared contract, and they are identical in both companion docs.

---

## 1. The big picture

```
 CVEngine (camera, models, rules)
        │  every analyzed frame:  on_frame(timestamp, flags, occupants)   flags = 1 byte, occupants = people count
        ▼
 ┌─────────────── SafetySystem (main.py) ───────────────────────────────────────┐
 │  edge detector: compares this frame's flags with the previous frame's flags   │
 │     0 → 1 on a bit   ⇒  on_event(ACTION, True,  timestamp)                    │
 │     1 → 0 on a bit   ⇒  on_event(ACTION, False, timestamp)                    │
 │    ├─ AlertEngine ──▶ Alert / AlertClear objects (returned)                   │
 │    └─ GameEngine  ──▶ DriveReport at end_drive()                              │
 └───────────────────────────────────────────────────────────────────────────────┘
```

- CVEngine sends **states** (one byte, many times per second). It does not send events and it does not know about alerts, levels or scores.
- The engines keep their existing API (`on_event`, `tick_all`, `dismiss`). **Nothing in `CAR_FRONTEND_INTERFACE.md` changes.** The new byte is turned into `on_event` calls by one thin layer in `SafetySystem` (§5).
- No strings cross this interface: the byte layout in §3 is the whole vocabulary. That avoids the name clash between CVEngine's own labels (`HANDS_OFF_THE_WHEEL`, `PHONE_AT_EAR`, ...) and the 8 canonical action keys.

---

## 2. The record (shared contract)

CVEngine sends **one record per analyzed frame**:

| Field | Type | Rules |
|---|---|---|
| `timestamp` | float, seconds | Monotonic, on the **same clock as `start_drive` / `end_drive`** timestamps. For a video replay it is the video time. Non-decreasing. Not a fixed rate: CVEngine may analyze every Nth camera frame (about 6 to 15 records per second today), so never assume a spacing. |
| `flags` | unsigned 8-bit integer, 0 to 255 | The byte in §3. |
| `occupants` | integer, 0 to 15 | How many people CVEngine sees in the car, front **and rear** seats, driver included. See §3.1. |

On the wire (the stdout stream of `live.py --stream`) one record is one line: `timestamp,flags,occupants`, for example `12.346,96,3` (12.346 s, hands off and eyes off road, three people in the car).

CVEngine sends nothing for frames it did not analyze. A missing record means "no new information", not "all clear" (see §4.5).

---

## 3. The byte (shared contract)

Most significant bit first. **A higher bit is a higher priority**, so a plain numeric comparison also sorts by priority.

| Bit | Mask | Meaning | Canonical action key | CVEngine status |
|---|---|---|---|---|
| 7 | `0x80` | fell asleep or unresponsive: eyes closed for longer than a blink, **or** posture slumped (head dropped to or below the shoulder line, e.g. fainted) | `EYES_CLOSED` (drowsiness is reported as `EYES_CLOSED`, see `CAR_FRONTEND_INTERFACE.md` §3) | **implemented** (on by default; `--no-sleep-bit` turns it off) |
| 6 | `0x40` | looked off the road | `EYES_OFF_ROAD` | **not implemented yet** |
| 5 | `0x20` | hands off the steering wheel | `HANDS_OFF_WHEEL` | **implemented** |
| 4 | `0x10` | distracted by mobile phone | `PHONE_USE` (see the note below) | live (`live_phone.PhoneMonitor` in `live.py`); confirmed state: about 1 s to switch on, about 3 s to switch off |
| 3 | `0x08` | yawning: the mouth opens wide, then a hand covers it (or it stays wide open >= 1.5 s) | `YAWN` | live (`yawn.py`, needs `--head face`; `--no-yawn` turns it off). A hand at the mouth alone is not a yawn (chin rest, smoking, eating look the same) |
| 2 to 0 | `0x07` | reserved | none | always sent as 0 |

Notes:
- **Phone:** one bit covers both "phone at the ear" and "phone in hand". The engines see it as `PHONE_USE`. `PHONE_CALL` stays available for the simulator, but CVEngine never produces it through this interface. The ear-versus-hand distinction is lost on purpose.
- **Reserved bits:** CVEngine sends them as 0. The engine side **masks them off and ignores them** (`flags & 0xF8`), so a future bit never breaks the old engine and a bad value never raises.
- `YAWN`, `EATING_DRINKING` and `SMOKING` have no bit. They remain simulator-only stubs.
- Examples: `0x20` = hands off only. `0x30` = hands off and phone. `0x00` = nothing detected.

### 3.1 The occupant count

- **What it is:** the number of people CVEngine detects in the cabin, smoothed over about 3 s so a person who is hidden for a moment does not make it flicker. It is counted per zone (driver, front passenger, rear seats) inside CVEngine, and only the total crosses this interface.
- **It is a lower bound.** A person hidden by a seat or by another person, lying down, or outside the camera's view is not counted. Like a `0` bit (§4.4), a count that is too low can mean "could not see", so never treat it as proof that nobody else is in the car.
- **It is not a safety bit.** It never raises an alert or changes a score by itself. It is context for the engines (for example, passengers present, or a forgotten child or pet check later). The engines may ignore it.
- **Rules:** non-negative integer, capped at 15. A value of `0` can also mean "the camera sees nobody or nothing". It changes slowly (the smoothing holds it for seconds), so engines can read it once per record without smoothing it again.

---

## 4. What a bit means (shared contract)

1. **A bit is a state, not an event.** `1` = the condition is present in this analyzed frame. `0` = it is not detected. The engines derive the start and the end themselves (§5).
2. **Lightly smoothed.** CVEngine suppresses flicker before it sets a bit (target: about **0.3 to 0.5 s**, to be tuned). CVEngine does **not** apply a duration hold of its own. The engines own every duration threshold: AlertEngine's escalation steps and GameEngine's `grace_seconds`. That way no delay is applied twice.
3. **Several bits may be 1 at once.** Each is independent. Priority (the bit order) only decides the order in which updates are produced (§5), never whether an action is reported.
4. **`0` never proves absence.** When CVEngine cannot see well enough (a hidden hand, no face found, wheel calibration unavailable), it sends `0` for that bit. CVEngine never sets a bit on uncertainty. The engines cannot tell "not happening" from "could not see", and the interface is deliberately lenient in that direction.
5. **Gaps.** If no record arrives for a long time (camera lost, CVEngine crashed), nothing is implied. See the open point in §8.

---

## 5. How the byte becomes engine calls (`SafetySystem.on_frame`)

New method in `main.py`:

```python
def on_frame(self, timestamp: float, flags: int, occupants: Optional[int] = None) -> List[AlertUpdate]:
    """One analyzed frame from CVEngine. Returns the AlertEngine's updates, like on_event."""
```

Rules:

1. **Validate, never raise.** If `flags` is not an `int` in `0..255` (a `bool` doesn't count), or `timestamp` is not a finite number, or `timestamp` is **smaller than the previous record's**, ignore the record and return `[]`. `occupants` is optional: if it is `None`, not an `int` in `0..15`, or a `bool`, the record is **not** rejected. The last valid count is kept and the flags are processed as usual.
2. **Mask:** `flags &= 0xF8`.
3. **Edge detection per bit.** Keep `previous_flags` (it is `0` after `start_drive`, after `end_drive` and at construction). For each bit whose value differs from `previous_flags`:
   - `0 → 1`: `on_event(ACTION, True, timestamp)`
   - `1 → 0`: `on_event(ACTION, False, timestamp)`
   Bits that did not change produce **no call**. (The engines accept repeated `True`, so sending every frame would also be correct. Edge-only keeps the engines quiet and cheap.)
4. **Order inside one record:** first all `1 → 0` calls, then all `0 → 1` calls. Within each group, highest bit first. This puts a clear before a new alert, as `CAR_FRONTEND_INTERFACE.md` §5 asks.
5. **Return value:** the concatenated updates of those `on_event` calls, in call order.
6. **Occupants:** store the latest valid `occupants` as `SafetySystem.occupants` (read-only, `0` after `start_drive`). It produces no `on_event` call and no alert. See §3.1.
7. `on_frame` feeds the GameEngine through `on_event`, exactly like any other event source. `on_event` stays available for the simulator.
8. `tick_all(timestamp)` is still called by the bridge about every 100 ms and does the escalations. `on_frame` does **not** need to tick.

---

## 6. What this means for the AlertEngine

- **The occurrence starts at the timestamp of the record where the bit became 1**, and there is no back-dating. CVEngine's smoothing therefore adds about 0.3 to 0.5 s before the first escalation step starts counting. Pick your first steps with that in mind: a step at `1 s` fires about 1.3 to 1.5 s after the behaviour really began.
- **Flicker is mostly gone, not entirely.** A bit that drops to 0 for one analyzed frame ends the occurrence and the next 1 starts a new one with a new `alert_id`. If this happens in practice, the right fix is a longer smoothing window in CVEngine, not a hysteresis inside the AlertEngine. Tell the CVEngine owner.
- **No suppression by priority.** If bits 5 and 4 are both 1, you report both alerts (`CAR_FRONTEND_INTERFACE.md` §5.1 rule 7). Priority is only the call order of §5.
- **Latched `EYES_CLOSED` (bit 7).** When the bit goes `1 → 0`, the latched alert stays until the driver presses Dismiss, as already specified. A bit that is `1` again after a dismiss is a new occurrence (rule 9 there). Nothing here changes that.
- **All five bits are produced live** (7, 6, 5, 4, 3). The configs of the other actions must still work, because the simulator can trigger them.
- **`DETECTED_ACTIONS`** (`main.py`): the actions CVEngine really produces. Today: `EYES_CLOSED`, `EYES_OFF_ROAD`, `HANDS_OFF_WHEEL`, `PHONE_USE` and `YAWN`.

---

## 7. Acceptance tests (please add them as unit tests)

Timestamps are seconds. `S` is a fresh `SafetySystem` after `start_drive(0.0)`.

1. `on_frame(0.0, 0x20)` → the HANDS_OFF_WHEEL occurrence starts (one `on_event(..., True, 0.0)` reaches both engines).
2. Then `on_frame(0.2, 0x20)`, `on_frame(0.4, 0x20)` → no new `on_event`, same `alert_id` as the first, no duplicate occurrence.
3. Then `on_frame(1.0, 0x00)` → exactly one `on_event("HANDS_OFF_WHEEL", False, 1.0)`.
4. `on_frame(0.0, 0x30)` → both `HANDS_OFF_WHEEL` and `PHONE_USE` start. Bit 4 produces `PHONE_USE`, never `PHONE_CALL`.
5. `0x20 → 0x10` in one record → the `HANDS_OFF_WHEEL` end is processed **before** the `PHONE_USE` start.
6. `on_frame(0.0, 0x07)` and `on_frame(0.1, 0x27)` → the reserved bits are ignored. The second behaves exactly like `0x20`.
7. `on_frame(0.0, 256)`, `on_frame(0.0, -1)`, `on_frame(0.0, "x")`, `on_frame(0.0, None)`, `on_frame(0.0, True)`, `on_frame(float("nan"), 0x20)` → `[]`, no exception, no state change.
8. `on_frame(5.0, 0x20)` then `on_frame(4.0, 0x00)` → the second record is ignored, and the action is still active.
9. `on_frame(0.0, 0x80)` then `on_frame(1.0, 0x00)` → `EYES_CLOSED` starts and ends. With the latched config, the alert stays after the `False` until `dismiss`.
10. `start_drive` and `end_drive` reset `previous_flags` to 0. After `end_drive`, a first record with `0x20` starts a fresh occurrence.
11. Same sequence of records twice → identical updates (deterministic).
12. `import main` still works in a clean Python 3.9 venv with nothing installed. `on_frame` uses the standard library only.
13. `on_frame(0.0, 0x20, 3)` → `S.occupants == 3` and the same `on_event` calls as `on_frame(0.0, 0x20)`; the count produces no alert. Then `on_frame(0.2, 0x20)` (no count), `on_frame(0.4, 0x20, 99)`, `on_frame(0.6, 0x20, True)`, `on_frame(0.8, 0x20, "x")` → `S.occupants` stays 3 and nothing raises.
14. `on_frame(1.0, 0x00, 0)` → `S.occupants == 0`. `start_drive` resets it to 0.

---

## 8. Open points (to settle with the CVEngine owner)

1. **Gap handling.** Proposal: if the next record arrives more than **2 s** after the previous one, the adapter treats the stream as interrupted, closes every open occurrence at the **previous** record's timestamp (`on_event(..., False, previous_timestamp)`), and clears `previous_flags`. Without this, a crashed CVEngine leaves an action active forever. The 2 s value matches CVEngine's own observation-loss time. Not decided yet.
2. **Smoothing window.** The 0.3 to 0.5 s figure is a target. CVEngine's live code currently has a 1.0 s hold inside its event machine and a raw per-frame `hands_off` output. The byte output, with light smoothing only, is **not built yet** on the CVEngine side.
3. **Phone granularity.** One bit means `PHONE_USE` and the ear-versus-hand distinction is lost. If scoring or alert texts should differ for a phone call, a reserved bit would have to be allocated for `PHONE_CALL` later.
4. **Availability.** The byte cannot say "this capability is unavailable" (for example, wheel calibration failed, so bit 5 is blind). It would just stay 0. If the app should ever show that, a reserved bit or a separate status message is needed.
