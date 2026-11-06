"""
AlertEngine - real-time driver safety alerting.

Receives driver action events (e.g. "PHONE_CALL", "EYES_OFF_ROAD") as they
happen and immediately returns the alert that should be shown to the driver.
Alerts escalate the longer a dangerous action lasts.

Action keys are canonical UPPER-case names (see ACTIONS). Incoming names are
normalized, so "phone_call" and "PHONE_CALL" are the same action.

Input  (per event): action name, whether it is active (started / still going)
                    or ended, and a timestamp in seconds.
Output (per event): an Alert object, or None if no alert is needed right now.
                    When an action that had raised an alert ends, an AlertClear
                    is passed to the optional `on_clear` callback.

Usage:
    engine = AlertEngine(on_clear=print)
    alert = engine.process_event("PHONE_CALL", is_active=True, timestamp=0.0)
    alerts = engine.tick_all(timestamp=3.0)     # call periodically for escalation
    alert = engine.process_event("PHONE_CALL", is_active=False, timestamp=5.0)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

# Canonical action keys. They are identical to the backend/app mistake types
# (e.g. "PHONE_CALL").
ACTIONS = (
    "PHONE_CALL",
    "EYES_OFF_ROAD",
    "EYES_CLOSED",
    "YAWN",
    "HANDS_OFF_WHEEL",
    "PHONE_USE",
    "EATING_DRINKING",
    "SMOKING",
)


def normalize_action(action: str) -> str:
    """Canonical form of an action name: stripped and upper-case."""
    return action.strip().upper()


# ---------------------------------------------------------------------------
# Alert types
# ---------------------------------------------------------------------------

class AlertLevel(IntEnum):
    """Severity of an alert. Higher = more intrusive."""
    NONE = 0
    VISUAL = 1      # icon / message on the display
    AUDIO = 2       # chime / voice prompt
    HAPTIC = 3      # seat / steering wheel vibration
    CRITICAL = 4    # loud alarm, possibly assisted intervention


@dataclass
class Alert:
    """Output of the AlertEngine - what the car should do right now."""
    action: str
    level: AlertLevel
    message: str
    duration_so_far: float   # seconds the dangerous action has been ongoing
    timestamp: float


@dataclass
class AlertClear:
    """Signal that the alert previously raised for `action` should be removed."""
    action: str
    last_level: AlertLevel   # highest level shown during this episode
    duration: float          # how long the dangerous action lasted (seconds)
    timestamp: float


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EscalationStep:
    """After `after_seconds` of an ongoing action, raise alert to `level`."""
    after_seconds: float
    level: AlertLevel
    message: str


@dataclass
class ActionAlertConfig:
    """How a single dangerous action should be alerted."""
    steps: List[EscalationStep]
    cooldown_seconds: float = 0.0   # min time between repeated alerts of same level


# TODO: tune thresholds and messages.
DEFAULT_ALERT_CONFIG: Dict[str, ActionAlertConfig] = {
    "PHONE_CALL": ActionAlertConfig(
        steps=[
            EscalationStep(0.0, AlertLevel.VISUAL, "Please put the phone down."),
            EscalationStep(3.0, AlertLevel.AUDIO, "Phone use detected - focus on the road."),
            EscalationStep(6.0, AlertLevel.HAPTIC, "Stop using the phone now!"),
        ],
    ),
    "EYES_OFF_ROAD": ActionAlertConfig(
        steps=[
            EscalationStep(1.0, AlertLevel.AUDIO, "Eyes on the road."),
            EscalationStep(2.5, AlertLevel.HAPTIC, "Look at the road!"),
            EscalationStep(4.0, AlertLevel.CRITICAL, "DANGER - eyes off road!"),
        ],
    ),
    "EYES_CLOSED": ActionAlertConfig(
        steps=[
            EscalationStep(0.5, AlertLevel.AUDIO, "Wake up - keep your eyes open."),
            EscalationStep(1.0, AlertLevel.HAPTIC, "Eyes closed - open your eyes!"),
            EscalationStep(2.0, AlertLevel.CRITICAL, "DANGER - you are falling asleep!"),
        ],
    ),
    "YAWN": ActionAlertConfig(
        steps=[
            EscalationStep(0.0, AlertLevel.VISUAL, "You seem tired - consider a break."),
        ],
    ),
    "HANDS_OFF_WHEEL": ActionAlertConfig(
        steps=[
            EscalationStep(1.0, AlertLevel.VISUAL, "Keep your hands on the wheel."),
            EscalationStep(3.0, AlertLevel.AUDIO, "Hands on the wheel, please."),
            EscalationStep(5.0, AlertLevel.HAPTIC, "Take the wheel now!"),
        ],
    ),
    "PHONE_USE": ActionAlertConfig(
        steps=[
            EscalationStep(0.0, AlertLevel.VISUAL, "Please put the phone away."),
            EscalationStep(2.0, AlertLevel.AUDIO, "Phone use detected - focus on the road."),
            EscalationStep(4.0, AlertLevel.HAPTIC, "Stop using the phone now!"),
        ],
    ),
    "EATING_DRINKING": ActionAlertConfig(
        steps=[
            EscalationStep(2.0, AlertLevel.VISUAL, "Eating or drinking - stay focused."),
            EscalationStep(8.0, AlertLevel.AUDIO, "Please finish eating when stopped."),
        ],
    ),
    "SMOKING": ActionAlertConfig(
        steps=[
            EscalationStep(2.0, AlertLevel.VISUAL, "Smoking detected - stay focused."),
            EscalationStep(10.0, AlertLevel.AUDIO, "Keep your attention on the road."),
        ],
    ),
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

@dataclass
class _ActiveAction:
    start_time: float
    current_level: AlertLevel = AlertLevel.NONE
    last_alert_time: float = field(default=float("-inf"))


class AlertEngine:
    def __init__(self, config: Optional[Dict[str, ActionAlertConfig]] = None,
                 on_clear: Optional[Callable[[AlertClear], None]] = None):
        source = config if config is not None else DEFAULT_ALERT_CONFIG
        self.config = {normalize_action(a): c for a, c in source.items()}
        self.on_clear = on_clear
        self._active: Dict[str, _ActiveAction] = {}

    # ---- public API -------------------------------------------------------

    def process_event(self, action: str, is_active: bool,
                      timestamp: Optional[float] = None) -> Optional[Alert]:
        """
        Handle one input event from the detection system.

        is_active=True  -> action started (or is still happening)
        is_active=False -> action ended. If it had raised an alert, an
                           AlertClear is passed to `on_clear`.
        """
        ts = time.monotonic() if timestamp is None else timestamp
        action = normalize_action(action)

        if action not in self.config:
            return None  # unknown / not dangerous action - ignore

        if not is_active:
            state = self._active.pop(action, None)
            if (state is not None and state.current_level > AlertLevel.NONE
                    and self.on_clear is not None):
                self.on_clear(AlertClear(action, state.current_level,
                                         ts - state.start_time, ts))
            return None

        if action not in self._active:
            self._active[action] = _ActiveAction(start_time=ts)

        return self._evaluate(action, ts)

    def evaluate(self, action: str, timestamp: Optional[float] = None) -> Optional[Alert]:
        """
        Check one active action for escalation and return its new alert, if
        any. Returns None if the action is not active or nothing changed.
        """
        ts = time.monotonic() if timestamp is None else timestamp
        action = normalize_action(action)
        if action not in self._active:
            return None
        return self._evaluate(action, ts)

    def tick_all(self, timestamp: Optional[float] = None) -> List[Alert]:
        """
        Call periodically (e.g. every 100 ms) so alerts escalate even when
        no new events arrive. Returns every new alert, most severe first.
        """
        ts = time.monotonic() if timestamp is None else timestamp
        alerts = [a for a in (self._evaluate(act, ts) for act in list(self._active)) if a]
        return sorted(alerts, key=lambda a: a.level, reverse=True)

    def tick(self, timestamp: Optional[float] = None) -> Optional[Alert]:
        """
        Like tick_all(), but returns only the most severe alert. Other alerts
        raised in the same tick are lost, so prefer tick_all().
        """
        alerts = self.tick_all(timestamp)
        return alerts[0] if alerts else None

    def active_actions(self) -> List[str]:
        return list(self._active)

    def reset(self) -> None:
        """Forget all active actions (no AlertClear signals are sent)."""
        self._active.clear()

    # ---- internals --------------------------------------------------------

    def _evaluate(self, action: str, ts: float) -> Optional[Alert]:
        state = self._active[action]
        cfg = self.config[action]
        duration = ts - state.start_time

        # Find the highest escalation step reached so far.
        step = None
        for s in sorted(cfg.steps, key=lambda s: s.after_seconds):
            if duration >= s.after_seconds:
                step = s
        if step is None:
            return None

        escalated = step.level > state.current_level
        cooled_down = ts - state.last_alert_time >= cfg.cooldown_seconds
        if not escalated and not (cfg.cooldown_seconds > 0 and cooled_down):
            return None  # already alerted at this level

        state.current_level = step.level
        state.last_alert_time = ts
        return Alert(action, step.level, step.message, duration, ts)


if __name__ == "__main__":
    engine = AlertEngine(on_clear=lambda c: print("   CLEAR", c))
    events = [
        ("PHONE_CALL", True, 0.0),
        ("PHONE_CALL", True, 3.5),
        ("EYES_OFF_ROAD", True, 4.0),
        ("PHONE_CALL", False, 5.0),
    ]
    for action, active, t in events:
        print(t, engine.process_event(action, active, t))
    print(6.0, engine.tick_all(6.0))
    print(8.5, engine.tick_all(8.5))
