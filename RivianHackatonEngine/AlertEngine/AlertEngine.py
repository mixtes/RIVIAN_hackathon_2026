"""
AlertEngine - real-time driver safety alarming (Python service <-> Kotlin app).

Rules
-----
* drowsiness       : starts on detector "start" event, rings until the user
                     dismisses it in the app (LATCHED). Detector "end" is ignored.
                     A new "start" right after dismiss rings again immediately.
                     Has PRIORITY: while it rings, the hands alarm is silenced.
* hands_off_wheel  : starts on detector "start" event, rings for 3 s (TIMED).
                     New "start" events during those 3 s are ignored.
                     After the 3 s: 1 s pause, then rings again if hands are
                     still off the wheel (i.e. no "end" event arrived).

Messages (JSON over WebSocket)
------------------------------
Python -> app : ALARM_START, ALARM_END, SNAPSHOT (sent on connect)
app -> Python : DISMISS {"alarm_id": ...}
(test only)   : DETECTION {"action": ..., "kind": "start"|"end"}

Detector input: the model runs in this same program (section 4b) and calls
submit_detection(action, kind). "ts" in messages is a monotonic clock
(seconds), used only for timing - not wall-clock time.

Run
---
    python alert_engine.py --simulate        # offline scenario, no deps
    python alert_engine.py --serve           # WebSocket server + real model (pip install websockets)
    python alert_engine.py --serve --fake    # WebSocket server + scripted fake detector
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


# ===========================================================================
# 1. Configuration
# ===========================================================================

HANDS = "hands_off_wheel"
DROWSY = "drowsiness"

HANDS_ALARM_DURATION_S = 3.0   # how long the hands alarm rings
HANDS_PAUSE_S = 1.0            # silence between two hands alarms
TICK_HZ = 10                   # how often the engine checks timers

WS_HOST = "0.0.0.0"
WS_PORT = 8765


# ===========================================================================
# 2. Outgoing messages
# ===========================================================================

@dataclass
class AlarmStart:
    alarm_id: str
    action: str
    mode: str                          # "TIMED" | "LATCHED"
    duration_s: Optional[float]        # only for TIMED
    ts: float
    type: str = "ALARM_START"


@dataclass
class AlarmEnd:
    alarm_id: str
    action: str
    reason: str                        # "timeout" | "dismissed" | "preempted"
    ts: float
    type: str = "ALARM_END"


Message = object  # AlarmStart | AlarmEnd


def to_json(msg) -> str:
    return json.dumps(asdict(msg))


# ===========================================================================
# 3. Engine (pure logic - no I/O, no real clock; easy to test)
# ===========================================================================

@dataclass
class _Alarm:
    alarm_id: str
    action: str
    start_ts: float
    end_ts: Optional[float] = None     # None = latched (until dismiss)


class AlertEngine:
    def __init__(self) -> None:
        self._counter: Dict[str, int] = {HANDS: 0, DROWSY: 0}
        self._drowsy_alarm: Optional[_Alarm] = None
        self._hands_alarm: Optional[_Alarm] = None
        self._hands_off = False                    # condition from detector
        self._hands_pause_until = float("-inf")

    # ---- 3.1 inputs --------------------------------------------------------

    def on_detection(self, action: str, kind: str, ts: float) -> List[Message]:
        """Detector event. kind = "start" | "end"."""
        out: List[Message] = []

        if action == DROWSY:
            if kind == "start" and self._drowsy_alarm is None:
                # Priority: silence the hands alarm first.
                if self._hands_alarm is not None:
                    out.append(self._end_hands(ts, reason="preempted"))
                self._drowsy_alarm = _Alarm(self._new_id(DROWSY), DROWSY, ts)
                out.append(AlarmStart(self._drowsy_alarm.alarm_id, DROWSY,
                                      "LATCHED", None, ts))
            # "end" for drowsiness is ignored by design.

        elif action == HANDS:
            self._hands_off = (kind == "start")
            out += self._maybe_start_hands(ts)

        return out

    def dismiss(self, alarm_id: str, ts: float) -> List[Message]:
        """User pressed 'dismiss' in the app."""
        out: List[Message] = []
        a = self._drowsy_alarm
        if a is not None and a.alarm_id == alarm_id:
            self._drowsy_alarm = None
            out.append(AlarmEnd(a.alarm_id, DROWSY, "dismissed", ts))
            # Hands still off? -> hands alarm can ring now.
            out += self._maybe_start_hands(ts)
        return out

    def tick(self, ts: float) -> List[Message]:
        """Call periodically (TICK_HZ). Handles the 3 s timer and the pause."""
        out: List[Message] = []
        a = self._hands_alarm
        if a is not None and ts >= a.end_ts:
            out.append(self._end_hands(a.end_ts, reason="timeout"))
            self._hands_pause_until = a.end_ts + HANDS_PAUSE_S
        out += self._maybe_start_hands(ts)
        return out

    def snapshot(self) -> dict:
        """All currently ringing alarms - sent to the app on (re)connect."""
        active = [a for a in (self._drowsy_alarm, self._hands_alarm) if a]
        return {"type": "SNAPSHOT",
                "active": [{"alarm_id": a.alarm_id, "action": a.action,
                            "start_ts": a.start_ts, "end_ts": a.end_ts}
                           for a in active]}

    # ---- 3.2 internals -----------------------------------------------------

    def _maybe_start_hands(self, ts: float) -> List[Message]:
        if (not self._hands_off
                or self._hands_alarm is not None       # already ringing
                or self._drowsy_alarm is not None      # drowsiness has priority
                or ts < self._hands_pause_until):      # 1 s pause
            return []
        end = ts + HANDS_ALARM_DURATION_S
        self._hands_alarm = _Alarm(self._new_id(HANDS), HANDS, ts, end)
        return [AlarmStart(self._hands_alarm.alarm_id, HANDS, "TIMED",
                           HANDS_ALARM_DURATION_S, ts)]

    def _end_hands(self, ts: float, reason: str) -> AlarmEnd:
        a = self._hands_alarm
        self._hands_alarm = None
        return AlarmEnd(a.alarm_id, HANDS, reason, ts)

    def _new_id(self, action: str) -> str:
        self._counter[action] += 1
        prefix = "hands" if action == HANDS else "drowsy"
        return f"{prefix}#{self._counter[action]}"


# ===========================================================================
# 4. WebSocket server (Python service <-> Kotlin app)
# ===========================================================================

# Thread-safe inbox for detector events. The model (running in its own thread
# in this same Python program) calls submit_detection(); the server loop
# drains the inbox on every tick.
_detections: "queue.Queue[tuple[str, str]]" = queue.Queue()


def submit_detection(action: str, kind: str) -> None:
    """Call from the model code, e.g. submit_detection("hands_off_wheel", "start")."""
    _detections.put((action, kind))


async def serve() -> None:
    import asyncio
    import websockets  # pip install websockets

    engine = AlertEngine()
    clients = set()
    detections = _detections

    async def broadcast(msgs: List[Message]) -> None:
        for m in msgs:
            data = to_json(m)
            print(data)
            for ws in list(clients):
                try:
                    await ws.send(data)
                except Exception:
                    clients.discard(ws)

    async def handler(ws) -> None:
        clients.add(ws)
        await ws.send(json.dumps(engine.snapshot()))
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "DISMISS":
                    await broadcast(engine.dismiss(msg["alarm_id"], time.monotonic()))
                elif msg.get("type") == "DETECTION":   # handy for testing
                    submit_detection(msg["action"], msg["kind"])
        finally:
            clients.discard(ws)

    async def loop() -> None:
        period = 1.0 / TICK_HZ
        while True:
            now = time.monotonic()
            while True:
                try:
                    action, kind = detections.get_nowait()
                except queue.Empty:
                    break
                await broadcast(engine.on_detection(action, kind, now))
            await broadcast(engine.tick(now))
            await asyncio.sleep(period)

    async with websockets.serve(handler, WS_HOST, WS_PORT):
        print(f"AlertEngine listening on ws://{WS_HOST}:{WS_PORT}")
        await loop()


# ===========================================================================
# 4b. Detector hook - plug the model in here
# ===========================================================================

def detector_loop() -> None:
    """
    Runs in its own thread. Replace the body with the real model loop:

        while True:
            frame = camera.read()
            for action, kind in model.process(frame):   # e.g. ("drowsiness", "start")
                submit_detection(action, kind)
    """
    pass


def fake_detector_loop() -> None:
    """Scripted events for testing the Kotlin app without the model (--fake)."""
    script = [
        (2.0,  HANDS,  "start"),
        (9.0,  HANDS,  "end"),
        (12.0, DROWSY, "start"),   # rings until DISMISS from the app
    ]
    t0 = time.monotonic()
    for at, action, kind in script:
        time.sleep(max(0.0, t0 + at - time.monotonic()))
        print(f"[fake detector] {action} {kind}")
        submit_detection(action, kind)


# ===========================================================================
# 5. Offline simulator (simulated clock)
# ===========================================================================

# (timestamp, kind, action/alarm_id, event)
SCENARIO = [
    (0.0,  "det", HANDS,     "start"),   # hands alarm #1
    (5.0,  "det", HANDS,     "end"),     # #2 (started at 4.0) still rings to 7.0
    (8.0,  "det", HANDS,     "start"),   # hands alarm #3
    (9.0,  "det", DROWSY,    "start"),   # drowsiness preempts hands
    (10.0, "det", DROWSY,    "start"),   # ignored - already ringing
    (12.0, "app", "drowsy#1", None),     # dismiss -> hands still off -> #4
    (12.5, "det", DROWSY,    "start"),   # rings again immediately
    (14.0, "det", HANDS,     "end"),
    (15.0, "app", "drowsy#2", None),     # dismiss -> nothing else rings
]


def simulate(end_ts: float = 17.0) -> None:
    engine = AlertEngine()
    events = sorted(SCENARIO, key=lambda e: e[0])
    i, t, dt = 0, 0.0, 1.0 / TICK_HZ
    while t <= end_ts + 1e-9:
        while i < len(events) and events[i][0] <= t + 1e-9:
            ts, src, who, kind = events[i]
            msgs = (engine.on_detection(who, kind, ts) if src == "det"
                    else engine.dismiss(who, ts))
            for m in msgs:
                print(f"{ts:5.1f}  {to_json(m)}")
            i += 1
        for m in engine.tick(round(t, 3)):
            print(f"{t:5.1f}  {to_json(m)}")
        t += dt


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--serve", action="store_true")
    p.add_argument("--simulate", action="store_true")
    p.add_argument("--fake", action="store_true",
                   help="with --serve: use scripted fake detector instead of the model")
    args = p.parse_args()
    if args.serve:
        import asyncio
        target = fake_detector_loop if args.fake else detector_loop
        threading.Thread(target=target, daemon=True).start()
        asyncio.run(serve())
    else:
        simulate()