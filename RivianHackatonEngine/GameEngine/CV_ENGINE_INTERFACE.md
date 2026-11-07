# GameEngine: the interface CVEngine uses to send detections

> For: the engine team (owners of `RivianHackatonEngine/`), and the CVEngine owner.
> Written: 2026-11-06.
> Companion docs: [`../AlertEngine/CV_ENGINE_INTERFACE.md`](../AlertEngine/CV_ENGINE_INTERFACE.md) (same contract, alert side), [`CAR_FRONTEND_INTERFACE.md`](CAR_FRONTEND_INTERFACE.md) (what the car app expects from this engine).
> Status: **proposal, agreed in principle with the CVEngine owner.** Sections 1 to 5 are the shared contract, and they are identical in both companion docs. Sections 6 to 8 are specific to the GameEngine.

---

## 1. The big picture

```
 CVEngine (camera, models, rules)
        │  every analyzed frame:  on_frame(timestamp, flags)        flags = 1 byte
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

CVEngine sends nothing for frames it did not analyze. A missing record means "no new information", not "all clear" (see §4.5).

---

## 3. The byte (shared contract)

Most significant bit first. **A higher bit is a higher priority**, so a plain numeric comparison also sorts by priority.

| Bit | Mask | Meaning | Canonical action key | CVEngine status |
|---|---|---|---|---|
| 7 | `0x80` | fell asleep | `EYES_CLOSED` (drowsiness is reported as `EYES_CLOSED`, see `CAR_FRONTEND_INTERFACE.md` §2) | **not implemented yet** |
| 6 | `0x40` | looked off the road | `EYES_OFF_ROAD` | **not implemented yet** |
| 5 | `0x20` | hands off the steering wheel | `HANDS_OFF_WHEEL` | **implemented** |
| 4 | `0x10` | distracted by mobile phone | `PHONE_USE` (see the note below) | live (`live_phone.PhoneMonitor` in `live.py`); confirmed state: about 1 s to switch on, about 3 s to switch off |
| 3 to 0 | `0x0F` | reserved | none | always sent as 0 |

Notes:
- **Phone:** one bit covers both "phone at the ear" and "phone in hand". The engines see it as `PHONE_USE`. `PHONE_CALL` stays available for the simulator, but CVEngine never produces it through this interface. The ear-versus-hand distinction is lost on purpose.
- **Reserved bits:** CVEngine sends them as 0. The engine side **masks them off and ignores them** (`flags & 0xF0`), so a future bit never breaks the old engine and a bad value never raises.
- `YAWN`, `EATING_DRINKING` and `SMOKING` have no bit. They remain simulator-only stubs.
- Examples: `0x20` = hands off only. `0x30` = hands off and phone. `0x00` = nothing detected.

---

## 4. What a bit means (shared contract)

1. **A bit is a state, not an event.** `1` = the condition is present in this analyzed frame. `0` = it is not detected. The engines derive the start and the end themselves (§5).
2. **Lightly smoothed.** CVEngine suppresses flicker before it sets a bit (target: about **0.3 to 0.5 s**, to be tuned). CVEngine does **not** apply a duration hold of its own. The engines own every duration threshold: AlertEngine's escalation steps and GameEngine's `grace_seconds`. That way no delay is applied twice.
3. **Several bits may be 1 at once.** Each is independent. Priority (the bit order) only decides the order in which updates are produced (§5), never whether an action is reported.
4. **`0` never proves absence.** When CVEngine cannot see well enough (a hidden hand, no face found, wheel calibration unavailable), it sends `0` for that bit. CVEngine never sets a bit on uncertainty. The engines cannot tell "not happening" from "could not see", and the interface is deliberately lenient in that direction.
5. **Gaps.** If no record arrives for a long time (camera lost, CVEngine crashed), nothing is implied. See the open point in §8.

---

## 5. How the byte becomes engine calls (`SafetySystem.on_frame`) (shared contract)

New method in `main.py`:

```python
def on_frame(self, timestamp: float, flags: int) -> List[AlertUpdate]:
    """One analyzed frame from CVEngine. Returns the AlertEngine's updates, like on_event."""
```

Rules:

1. **Validate, never raise.** If `flags` is not an `int` in `0..255` (a `bool` doesn't count), or `timestamp` is not a finite number, or `timestamp` is **smaller than the previous record's**, ignore the record and return `[]`.
2. **Mask:** `flags &= 0xF0`.
3. **Edge detection per bit.** Keep `previous_flags` (it is `0` after `start_drive`, after `end_drive` and at construction). For each bit whose value differs from `previous_flags`:
   - `0 → 1`: `on_event(ACTION, True, timestamp)`
   - `1 → 0`: `on_event(ACTION, False, timestamp)`
   Bits that did not change produce **no call**. (The engines accept repeated `True`, so sending every frame would also be correct. Edge-only keeps the engines quiet and cheap.)
4. **Order inside one record:** first all `1 → 0` calls, then all `0 → 1` calls. Within each group, highest bit first. This puts a clear before a new alert, as `AlertEngine/CAR_FRONTEND_INTERFACE.md` §5 asks.
5. **Return value:** the concatenated updates of those `on_event` calls, in call order.
6. `on_frame` feeds the GameEngine through `on_event`, exactly like any other event source. `on_event` stays available for the simulator.
7. `tick_all(timestamp)` is still called by the bridge about every 100 ms and does the escalations. `on_frame` does **not** need to tick.

---

## 6. What this means for the GameEngine

- **The GameEngine input is unchanged.** It keeps receiving `process_event(action, is_active, timestamp)`. Everything in `CAR_FRONTEND_INTERFACE.md` §3.1 still holds, including idempotent repeated `True`.
- **An occurrence runs from the timestamp of the record where the bit became 1 to the timestamp of the record where it became 0.** There is no back-dating. CVEngine's smoothing delays **both** edges by up to its window (about 0.3 to 0.5 s), so durations are nearly unbiased but the occurrence sits up to about half a second late in time. If CVEngine's smoothing were asymmetric (a long delay on one edge only), every duration would be biased by the difference. Ask the CVEngine owner to keep it symmetric.
- **`grace_seconds` is the only hold.** CVEngine adds none, so an occurrence shorter than its grace is still recorded in `occurrences` and costs nothing (`score_occurrence == 0`), exactly as `CAR_FRONTEND_INTERFACE.md` §3.1 rule 4 describes.
- **Unseen moments are never penalized.** CVEngine sends `0` when it can't tell (§4.4). So the score can only be too lenient, never wrongly harsh, from an undetected hand or face. Keep that in mind when you tune penalties: a driver CVEngine can't see well scores better.
- **Simultaneous bits: only the most severe one is penalized** (overlap rule, `GameEngine.PRIORITY`: `EYES_CLOSED > PHONE_CALL > PHONE_USE > EYES_OFF_ROAD > HANDS_OFF_WHEEL > EATING_DRINKING > SMOKING > YAWN`). `0x30` still opens two occurrences (`HANDS_OFF_WHEEL` and `PHONE_USE`), and both are in `occurrences`. But each occurrence is penalized only for the seconds that no higher-priority occurrence covers, so here `PHONE_USE` is penalized and the fully covered `HANDS_OFF_WHEEL` costs nothing (`count == 0`, `score_occurrence == 0`). This scoring priority is separate from the bit order in §5, which only orders the updates.
- **A bit still `1` at the end.** `end_drive(ts)` closes it at `ts`, as already specified.
- **Danger: a stream that stops while a bit is `1`.** If CVEngine dies or the camera drops with a bit set, the occurrence stays open and is scored up to `end_drive`, which can be the rest of the whole drive. This is the main reason for the gap rule in §8.1. For the score it matters more than for the alerts.
- **Not implemented yet.** Bits 7 and 6 are never set today. `EYES_CLOSED` and `EYES_OFF_ROAD` scoring must still work, because the simulator can trigger them and they will be wired in later.
- **Dismissing an alert never changes the score** (`CAR_FRONTEND_INTERFACE.md` §3.1 rule 6). It has no connection to this interface.
- **Offline replay.** `GameEngine.run_from_csv` and its event CSV are unchanged. If a byte-per-frame file is ever replayed offline, apply the edge rules of §5 first and feed the resulting events.

---

## 7. Acceptance tests (please add them as unit tests)

Timestamps are seconds. `S` is a fresh `SafetySystem` after `start_drive(0.0)`. Assume `HANDS_OFF_WHEEL` has `grace_seconds = 2.0`; adjust the numbers to your real config.

1. Records `(1.0, 0x20)`, `(1.2, 0x20)`, ..., `(4.0, 0x20)`, `(4.2, 0x00)`, then `end_drive(10.0)` → one `HANDS_OFF_WHEEL` occurrence from 1.0 to 4.2 (duration 3.2 s). `actions["HANDS_OFF_WHEEL"].count == 1`.
2. Records `(1.0, 0x20)`, `(1.5, 0x00)` → the occurrence (0.5 s) is in `occurrences`, but `count == 0` and `score_occurrence(occ) == 0`.
3. `(0.0, 0x30)` then `(5.0, 0x00)` → both `HANDS_OFF_WHEEL` and `PHONE_USE` have an occurrence from 0.0 to 5.0. Only `PHONE_USE` is penalized (overlap rule, §6): `HANDS_OFF_WHEEL` and `PHONE_CALL` have `count == 0`.
4. `0x0F`, `0x2F`, `256`, `-1`, `"x"`, `None`, `True`, a NaN timestamp → the invalid ones leave no occurrence behind, nothing raises. `0x2F` is scored exactly like `0x20`.
5. `(5.0, 0x20)` then `(4.0, 0x00)` (timestamp goes backwards) → the second record is ignored, and the occurrence is still open.
6. `(2.0, 0x20)` as the last record, then `end_drive(10.0)` → the occurrence is closed at 10.0 (duration 8.0).
7. `(2.0, 0x20)`, then a record at `(30.0, 0x20)` → **if the gap rule of §8.1 is adopted**, the first occurrence is closed at 2.0 and a new one starts at 30.0. Otherwise it is one occurrence from 2.0 on. Write this test together with that decision.
8. `(0.0, 0x80)`, `(3.0, 0x00)` → an `EYES_CLOSED` occurrence of 3.0 s, scored by how long the bit was `1`, not by any alarm.
9. Same records twice → identical `DriveReport` (deterministic). `0 <= score <= 100`.
10. `end_drive()` without `start_drive()` after some `on_frame` calls → a valid report, no exception.

---

## 8. Open points (to settle with the CVEngine owner)

1. **Gap handling** (more important here than for alerts, see §6). Proposal: if the next record arrives more than **2 s** after the previous one, the adapter closes every open occurrence at the **previous** record's timestamp (`on_event(..., False, previous_timestamp)`) and clears `previous_flags`. This keeps an outage from being scored as a violation. The 2 s value matches CVEngine's own observation-loss time. Not decided yet. The AlertEngine doc has the same open point.
2. **Smoothing window and symmetry.** The 0.3 to 0.5 s figure is a target. CVEngine's live code currently has a 1.0 s hold inside its event machine and a raw per-frame `hands_off` output. The byte output, with light and symmetric smoothing, is **not built yet** on the CVEngine side.
3. **Phone granularity.** One bit means `PHONE_USE`, so a phone call and phone-in-hand score the same. If they should score differently, a reserved bit would have to be allocated for `PHONE_CALL` later.
4. **Leniency.** A driver CVEngine cannot see well is not penalized (§6). If you would rather treat long unobservable periods differently, the byte would need an "unobservable" flag, which it cannot carry today.
