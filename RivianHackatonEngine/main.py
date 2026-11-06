"""
Feeds the same driver-action events to both engines in parallel:
  - AlertEngine returns an alert immediately (real time)
  - GameEngine only records; its score comes out at end_drive()
"""

from typing import Callable, List, Optional

from AlertEngine.AlertEngine import Alert, AlertClear, AlertEngine
from GameEngine.GameEngine import DriveReport, GameEngine

__version__ = "0.2.0"


class SafetySystem:
    def __init__(self, on_clear: Optional[Callable[[AlertClear], None]] = None):
        self.alerts = AlertEngine(on_clear=on_clear)
        self.game = GameEngine()

    def start_drive(self, timestamp: Optional[float] = None) -> None:
        self.alerts.reset()
        self.game.start_drive(timestamp)

    def on_event(self, action: str, is_active: bool,
                 timestamp: Optional[float] = None) -> Optional[Alert]:
        self.game.process_event(action, is_active, timestamp)
        return self.alerts.process_event(action, is_active, timestamp)

    def tick_all(self, timestamp: Optional[float] = None) -> List[Alert]:
        """Every alert raised by escalation this tick, most severe first."""
        return self.alerts.tick_all(timestamp)

    def tick(self, timestamp: Optional[float] = None) -> Optional[Alert]:
        """Only the most severe alert this tick. Prefer tick_all()."""
        return self.alerts.tick(timestamp)

    def end_drive(self, timestamp: Optional[float] = None) -> DriveReport:
        self.alerts.reset()
        return self.game.end_drive(timestamp)


if __name__ == "__main__":
    system = SafetySystem(on_clear=lambda c: print(
        f"[{c.timestamp:>5.1f}s] CLEAR {c.action} (was {c.last_level.name})"))
    system.start_drive(0.0)
    for action, active, t in [
        ("PHONE_CALL", True, 10.0),
        ("PHONE_CALL", True, 13.5),
        ("PHONE_CALL", False, 18.0),
        ("EYES_OFF_ROAD", True, 30.0),
        ("HANDS_OFF_WHEEL", True, 31.0),
    ]:
        alert = system.on_event(action, active, t)
        if alert:
            print(f"[{t:>5.1f}s] ALERT {alert.level.name}: {alert.message}")
    for alert in system.tick_all(34.5):
        print(f"[ 34.5s] ALERT {alert.level.name}: {alert.message}")
    system.on_event("EYES_OFF_ROAD", False, 35.0)
    system.on_event("HANDS_OFF_WHEEL", False, 36.0)
    print()
    print(f"engine {__version__}")
    print(system.end_drive(120.0))
