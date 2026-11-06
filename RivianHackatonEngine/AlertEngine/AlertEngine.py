"""
AlertEngine - real-time driver safety alerting.

Receives driver action events (e.g. "phone_call", "eyes_off_road") as they
happen and immediately returns the alert that should be shown to the driver.
Alerts escalate the longer a dangerous action lasts.

Input  (per event): action name, whether it is active (started / still going)
                    or ended, and a timestamp in seconds.
Output (per event): an Alert object, or None if no alert is needed right now.

Usage:
    engine = AlertEngine()
    alert = engine.process_event("phone_call", is_active=True, timestamp=0.0)
    alert = engine.tick(timestamp=3.0)          # call periodically for escalation
    alert = engine.process_event("phone_call", is_active=False, timestamp=5.0)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional


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


# TODO: define real actions, thresholds and messages.
DEFAULT_ALERT_CONFIG: Dict[str, ActionAlertConfig] = {
    "phone_call": ActionAlertConfig(
        steps=[
            EscalationStep(0.0, AlertLevel.VISUAL, "Please put the phone down."),
            EscalationStep(3.0, AlertLevel.AUDIO, "Phone use detected - focus on the road."),
            EscalationStep(6.0, AlertLevel.HAPTIC, "Stop using the phone now!"),
        ],
    ),
    "eyes_off_road": ActionAlertConfig(
        steps=[
            EscalationStep(1.0, AlertLevel.AUDIO, "Eyes on the road."),
            EscalationStep(2.5, AlertLevel.HAPTIC, "Look at the road!"),
            EscalationStep(4.0, AlertLevel.CRITICAL, "DANGER - eyes off road!"),
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
    def __init__(self, config: Optional[Dict[str, ActionAlertConfig]] = None):
        self.config = config if config is not None else DEFAULT_ALERT_CONFIG
        self._active: Dict[str, _ActiveAction] = {}

    # ---- public API -------------------------------------------------------

    def process_event(self, action: str, is_active: bool,
                      timestamp: Optional[float] = None) -> Optional[Alert]:
        """
        Handle one input event from the detection system.

        is_active=True  -> action started (or is still happening)
        is_active=False -> action ended
        """
        ts = time.monotonic() if timestamp is None else timestamp

        if action not in self.config:
            return None  # unknown / not dangerous action - ignore

        if not is_active:
            self._active.pop(action, None)
            return None  # TODO: optionally emit a "clear alert" signal

        if action not in self._active:
            self._active[action] = _ActiveAction(start_time=ts)

        return self._evaluate(action, ts)

    def tick(self, timestamp: Optional[float] = None) -> Optional[Alert]:
        """
        Call periodically (e.g. every 100 ms) so alerts escalate even when
        no new events arrive. Returns the most severe alert, if any.
        """
        ts = time.monotonic() if timestamp is None else timestamp
        alerts = [a for a in (self._evaluate(act, ts) for act in list(self._active)) if a]
        return max(alerts, key=lambda a: a.level, default=None)

    def active_actions(self) -> List[str]:
        return list(self._active)

    def reset(self) -> None:
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
    engine = AlertEngine()
    events = [
        ("phone_call", True, 0.0),
        ("phone_call", True, 3.5),
        ("eyes_off_road", True, 4.0),
        ("phone_call", False, 5.0),
    ]
    for action, active, t in events:
        print(t, engine.process_event(action, active, t))
    print(6.0, engine.tick(6.0))
    print(8.5, engine.tick(8.5))
