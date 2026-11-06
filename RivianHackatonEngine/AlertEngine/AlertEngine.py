"""
AlertEngine - real-time driver safety alerting (pure Python library).

Contract: AlertEngine/CAR_FRONTEND_INTERFACE.md (agreed with the car app).

Receives driver-action events (e.g. "PHONE_CALL") and returns, from every
call, the list of alert changes the car app must show:

  * Alert       - new occurrence or a higher level (same alert_id per occurrence)
  * AlertClear  - remove an alert ("ended" by the detector or "dismissed" by the driver)

No networking, threads, sleeping, printing or wall clock: all timing comes from
the `timestamp` argument (monotonic float seconds; None -> time.monotonic()).
The WebSocket bridge (simulated_game_engine/) turns the results into messages.

Rules
-----
* Each occurrence (is_active True ... False) gets one uuid4 alert_id. Repeated
  True is idempotent. An Alert is returned only when the level goes up.
* Non-latched action: False -> AlertClear(reason="ended") if it had alerted.
* Latched action (EYES_CLOSED = drowsiness): alerts carry requires_ack=True and
  stay after False; only dismiss(alert_id) removes them. If the action is still
  active at dismiss time, a new occurrence (new id) starts right away.
* No priority suppression: the app orders alerts by level itself.

Usage:
    engine = AlertEngine()
    engine.process_event("PHONE_CALL", True, 0.0)    # -> [Alert(VISUAL)]
    engine.tick_all(3.0)                             # -> [Alert(AUDIO)]
    engine.process_event("PHONE_CALL", False, 5.0)   # -> [AlertClear("ended")]

Run `python AlertEngine.py` for an offline demo with a simulated clock.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

# The 8 canonical action keys, shared with the GameEngine, bridge, car app and
# backend. Fixed: don't add, rename or drop any.
ACTIONS = (
    "PHONE_CALL",
    "PHONE_USE",
    "HANDS_OFF_WHEEL",
    "EYES_CLOSED",       # also drowsiness (there is no DROWSINESS type)
    "EYES_OFF_ROAD",
    "YAWN",
    "EATING_DRINKING",
    "SMOKING",
)


def normalize_action(action: str) -> str:
    """Canonical form of an action name: stripped and upper-case."""
    return action.strip().upper()


def _now(timestamp: Optional[float]) -> float:
    return time.monotonic() if timestamp is None else float(timestamp)


# ---------------------------------------------------------------------------
# Types (names are read by the bridge - don't rename)
# ---------------------------------------------------------------------------

class AlertLevel(IntEnum):
    NONE = 0       # internal only: never put in an Alert
    VISUAL = 1     # amber banner
    AUDIO = 2      # amber banner + chime + voice
    HAPTIC = 3     # red pulsing banner + double chime + voice
    CRITICAL = 4   # full-screen red overlay + repeating alarm


@dataclass
class Alert:
    alert_id: str            # same id for every escalation of one occurrence
    action: str              # one of ACTIONS, UPPER_CASE
    level: AlertLevel        # VISUAL..CRITICAL, never NONE
    message: str             # shown on screen and spoken by TTS
    duration_so_far: float   # seconds the action has lasted (>= 0)
    timestamp: float         # monotonic; occurrence start = timestamp - duration_so_far
    requires_ack: bool = False   # True = latched: stays until the driver dismisses


@dataclass
class AlertClear:
    alert_id: str
    action: str
    last_level: AlertLevel   # highest level shown in this occurrence
    duration: float          # how long the occurrence lasted (seconds)
    timestamp: float
    reason: str              # "ended" | "dismissed"


AlertUpdate = Union[Alert, AlertClear]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EscalationStep:
    after_seconds: float     # time since the occurrence started
    level: AlertLevel
    message: str


@dataclass
class ActionAlertConfig:
    steps: List[EscalationStep]   # sorted by after_seconds; levels strictly increasing
    latched: bool = False         # True = alerts need a driver Dismiss (requires_ack)


# Messages: short (< ~40 chars), plain English, no emoji - they are spoken.
# TODO: tune thresholds and messages.
DEFAULT_ALERT_CONFIG: Dict[str, ActionAlertConfig] = {
    "PHONE_CALL": ActionAlertConfig(steps=[
        EscalationStep(0.0, AlertLevel.VISUAL, "Please put the phone down."),
        EscalationStep(3.0, AlertLevel.AUDIO, "Phone call detected. Eyes on the road."),
        EscalationStep(6.0, AlertLevel.HAPTIC, "Stop using the phone now!"),
        EscalationStep(10.0, AlertLevel.CRITICAL, "Hang up and focus on driving!"),
    ]),
    "PHONE_USE": ActionAlertConfig(steps=[
        EscalationStep(0.0, AlertLevel.VISUAL, "Please put the phone away."),
        EscalationStep(2.0, AlertLevel.AUDIO, "Phone use detected. Eyes on the road."),
        EscalationStep(4.0, AlertLevel.HAPTIC, "Stop using the phone now!"),
        EscalationStep(8.0, AlertLevel.CRITICAL, "Put the phone away now!"),
    ]),
    # Replaces the old TIMED rule (3 s on / 1 s pause): the app repeats the
    # CRITICAL tone every 2 s by itself.
    "HANDS_OFF_WHEEL": ActionAlertConfig(steps=[
        EscalationStep(1.0, AlertLevel.VISUAL, "Keep your hands on the wheel."),
        EscalationStep(3.0, AlertLevel.AUDIO, "Hands on the wheel, please."),
        EscalationStep(6.0, AlertLevel.HAPTIC, "Take the wheel now!"),
        EscalationStep(10.0, AlertLevel.CRITICAL, "Danger! Hold the wheel!"),
    ]),
    # Drowsiness: latched, rings until the driver presses Dismiss.
    "EYES_CLOSED": ActionAlertConfig(latched=True, steps=[
        EscalationStep(0.5, AlertLevel.CRITICAL, "Wake up! Pull over and rest."),
    ]),
    "EYES_OFF_ROAD": ActionAlertConfig(steps=[  # STUB: not detected yet
        EscalationStep(1.0, AlertLevel.AUDIO, "Eyes on the road."),
        EscalationStep(2.5, AlertLevel.HAPTIC, "Look at the road!"),
        EscalationStep(4.0, AlertLevel.CRITICAL, "Danger! Eyes off the road!"),
    ]),
    "YAWN": ActionAlertConfig(steps=[  # STUB: not detected yet
        EscalationStep(0.0, AlertLevel.VISUAL, "You seem tired. Consider a break."),
    ]),
    "EATING_DRINKING": ActionAlertConfig(steps=[  # STUB: not detected yet
        EscalationStep(2.0, AlertLevel.VISUAL, "Eating or drinking. Stay focused."),
        EscalationStep(8.0, AlertLevel.AUDIO, "Please eat when you are stopped."),
    ]),
    "SMOKING": ActionAlertConfig(steps=[  # STUB: not detected yet
        EscalationStep(2.0, AlertLevel.VISUAL, "Smoking detected. Stay focused."),
        EscalationStep(10.0, AlertLevel.AUDIO, "Keep your attention on the road."),
    ]),
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

@dataclass
class _Occurrence:
    alert_id: str
    action: str
    start_time: float
    latched: bool
    end_time: Optional[float] = None       # set when the detector says False
    level: AlertLevel = AlertLevel.NONE    # highest level raised so far
    last_alert: Optional[Alert] = None     # latest Alert sent for this occurrence


class AlertEngine:
    """Pure, deterministic, single-threaded. Every call returns what changed."""

    def __init__(self, config: Optional[Dict[str, ActionAlertConfig]] = None):
        source = config if config is not None else DEFAULT_ALERT_CONFIG
        self.config: Dict[str, ActionAlertConfig] = {}
        for action, cfg in source.items():
            steps = sorted(cfg.steps, key=lambda s: s.after_seconds)
            self.config[normalize_action(action)] = ActionAlertConfig(steps, cfg.latched)
        # Occurrences whose action is currently active, by action.
        self._open: Dict[str, _Occurrence] = {}
        # Latched occurrences that raised an alert and wait for dismiss, by id.
        self._held: Dict[str, _Occurrence] = {}

    # ---- public API -------------------------------------------------------

    def process_event(self, action: str, is_active: bool,
                      timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Detector input. True = action started / still going, False = ended."""
        ts = _now(timestamp)
        try:
            action = normalize_action(action)
        except AttributeError:
            return []
        if action not in self.config:
            return []  # unknown action - ignore, never raise

        if is_active:
            occ = self._open.get(action)
            if occ is None:
                occ = self._start(action, ts)
            return self._evaluate(occ, ts)

        occ = self._open.pop(action, None)
        if occ is None or occ.level == AlertLevel.NONE:
            return []
        occ.end_time = ts
        if occ.latched:
            return []  # stays in _held until dismiss
        return [self._clear(occ, ts, "ended")]

    def tick_all(self, timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Call periodically (~100 ms). Returns escalations of all active actions."""
        ts = _now(timestamp)
        out: List[AlertUpdate] = []
        for occ in list(self._open.values()):
            out += self._evaluate(occ, ts)
        return out

    def dismiss(self, alert_id: str, timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Driver pressed Dismiss. Only latched alerts can be dismissed."""
        ts = _now(timestamp)
        occ = self._held.pop(alert_id, None) if isinstance(alert_id, str) else None
        if occ is None:
            return []
        out: List[AlertUpdate] = [self._clear(occ, ts, "dismissed")]
        if self._open.get(occ.action) is occ:
            # Still active: a new occurrence starts now (rings again if it lasts).
            out += self._evaluate(self._start(occ.action, ts), ts)
        return out

    def active_alerts(self) -> List[Alert]:
        """Latest Alert of every alert still showing (for reconnect / keep-alive)."""
        seen = set()
        out: List[Alert] = []
        for occ in list(self._open.values()) + list(self._held.values()):
            if occ.last_alert is not None and occ.alert_id not in seen:
                seen.add(occ.alert_id)
                out.append(occ.last_alert)
        return out

    def active_actions(self) -> List[str]:
        """Actions currently reported as active by the detector."""
        return list(self._open)

    def reset(self) -> None:
        """Forget everything (drive start / end). Emits nothing."""
        self._open.clear()
        self._held.clear()

    # ---- internals --------------------------------------------------------

    def _start(self, action: str, ts: float) -> _Occurrence:
        occ = _Occurrence(str(uuid.uuid4()), action, ts, self.config[action].latched)
        self._open[action] = occ
        return occ

    def _evaluate(self, occ: _Occurrence, ts: float) -> List[AlertUpdate]:
        duration = max(0.0, ts - occ.start_time)
        step = None
        for s in self.config[occ.action].steps:
            if duration >= s.after_seconds:
                step = s
        if step is None or step.level <= occ.level:
            return []  # nothing reached yet, or no level increase
        occ.level = step.level
        occ.last_alert = Alert(occ.alert_id, occ.action, step.level, step.message,
                               duration, ts, requires_ack=occ.latched)
        if occ.latched:
            self._held[occ.alert_id] = occ
        return [occ.last_alert]

    @staticmethod
    def _clear(occ: _Occurrence, ts: float, reason: str) -> AlertClear:
        end = occ.end_time if occ.end_time is not None else ts
        return AlertClear(occ.alert_id, occ.action, occ.level,
                          max(0.0, end - occ.start_time), ts, reason)


# ---------------------------------------------------------------------------
# Offline demo (simulated clock) - dev only, never runs on import
# ---------------------------------------------------------------------------

def _demo() -> None:
    engine = AlertEngine()
    script = [  # (timestamp, kind, action, is_active)
        (0.0, "det", "hands_off_wheel", True),
        (4.0, "det", "PHONE_CALL", True),
        (7.0, "det", "HANDS_OFF_WHEEL", False),
        (8.0, "det", "PHONE_CALL", False),
        (9.0, "det", "EYES_CLOSED", True),
        (10.0, "det", "EYES_CLOSED", False),   # latched: alert stays
        (12.0, "dismiss", "EYES_CLOSED", None),
    ]
    t, i = 0.0, 0
    while t <= 13.0 + 1e-9:
        t = round(t, 3)
        while i < len(script) and script[i][0] <= t + 1e-9:
            _, kind, action, active = script[i]
            if kind == "det":
                updates = engine.process_event(action, active, t)
            else:
                ids = [a.alert_id for a in engine.active_alerts() if a.action == action]
                updates = engine.dismiss(ids[0], t) if ids else []
            for u in updates:
                print(f"{t:5.1f}  {u}")
            i += 1
        for u in engine.tick_all(t):
            print(f"{t:5.1f}  {u}")
        t += 0.1


if __name__ == "__main__":
    _demo()
