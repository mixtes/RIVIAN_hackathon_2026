"""
Feeds the same driver-action events to both engines in parallel:
  - AlertEngine returns alert updates immediately (real time)
  - GameEngine only records; its score comes out at end_drive()

Contract: AlertEngine/CAR_FRONTEND_INTERFACE.md and GameEngine/CAR_FRONTEND_INTERFACE.md.
"""

from __future__ import annotations

from typing import List, Optional

from AlertEngine.AlertEngine import (Alert, AlertClear, AlertEngine, AlertLevel,
                                     AlertUpdate)
from GameEngine.GameEngine import DriveReport, GameEngine

__version__ = "0.3.0"

# The subset of the 8 canonical actions that real detection (CVEngine) emits.
DETECTED_ACTIONS = ("PHONE_CALL", "PHONE_USE", "HANDS_OFF_WHEEL")

__all__ = ["SafetySystem", "Alert", "AlertClear", "AlertLevel", "AlertUpdate",
           "DriveReport", "DETECTED_ACTIONS", "__version__"]


class SafetySystem:
    def __init__(self) -> None:
        self.alerts = AlertEngine()
        self.game = GameEngine()

    def start_drive(self, timestamp: Optional[float] = None) -> None:
        self.alerts.reset()
        self.game.start_drive(timestamp)

    def on_event(self, action: str, is_active: bool,
                 timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Feeds both engines; returns the AlertEngine's updates."""
        self.game.process_event(action, is_active, timestamp)
        return self.alerts.process_event(action, is_active, timestamp)

    def tick_all(self, timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Escalations of all active actions since the last call."""
        return self.alerts.tick_all(timestamp)

    def dismiss(self, alert_id: str, timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Driver dismissed a latched alert. AlertEngine only: never changes the score."""
        return self.alerts.dismiss(alert_id, timestamp)

    def end_drive(self, timestamp: Optional[float] = None) -> DriveReport:
        self.alerts.reset()
        return self.game.end_drive(timestamp)


def _show(t: float, updates: List[AlertUpdate]) -> None:
    for u in updates:
        if isinstance(u, Alert):
            ack = " [needs dismiss]" if u.requires_ack else ""
            print(f"[{t:>5.1f}s] ALERT {u.action} {u.level.name}: {u.message}{ack}")
        else:
            print(f"[{t:>5.1f}s] CLEAR {u.action} ({u.reason}, was {u.last_level.name})")


if __name__ == "__main__":
    system = SafetySystem()
    system.start_drive(0.0)
    for action, active, t in [
        ("PHONE_CALL", True, 10.0),
        ("PHONE_CALL", True, 13.5),
        ("PHONE_CALL", False, 18.0),
        ("EYES_OFF_ROAD", True, 30.0),
        ("HANDS_OFF_WHEEL", True, 31.0),
    ]:
        _show(t, system.on_event(action, active, t))
    _show(34.5, system.tick_all(34.5))
    _show(35.0, system.on_event("EYES_OFF_ROAD", False, 35.0))
    _show(36.0, system.on_event("HANDS_OFF_WHEEL", False, 36.0))
    _show(40.0, system.on_event("EYES_CLOSED", True, 40.0))
    _show(41.0, system.tick_all(41.0))
    _show(42.0, system.on_event("EYES_CLOSED", False, 42.0))
    for a in system.alerts.active_alerts():
        _show(45.0, system.dismiss(a.alert_id, 45.0))
    print()
    print(f"engine {__version__}")
    print(system.end_drive(120.0))
