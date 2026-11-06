"""
Feeds the same driver-action events to both engines in parallel:
  - AlertEngine returns an alert immediately (real time)
  - GameEngine only records; its score comes out at end_drive()
"""

from typing import Optional

from AlertEngine.AlertEngine import Alert, AlertEngine
from GameEngine.GameEngine import DriveReport, GameEngine


class SafetySystem:
    def __init__(self):
        self.alerts = AlertEngine()
        self.game = GameEngine()

    def start_drive(self, timestamp: Optional[float] = None) -> None:
        self.alerts.reset()
        self.game.start_drive(timestamp)

    def on_event(self, action: str, is_active: bool,
                 timestamp: Optional[float] = None) -> Optional[Alert]:
        self.game.process_event(action, is_active, timestamp)
        return self.alerts.process_event(action, is_active, timestamp)

    def tick(self, timestamp: Optional[float] = None) -> Optional[Alert]:
        return self.alerts.tick(timestamp)

    def end_drive(self, timestamp: Optional[float] = None) -> DriveReport:
        self.alerts.reset()
        return self.game.end_drive(timestamp)


if __name__ == "__main__":
    system = SafetySystem()
    system.start_drive(0.0)
    for action, active, t in [
        ("phone_call", True, 10.0),
        ("phone_call", True, 13.5),
        ("phone_call", False, 18.0),
        ("eyes_off_road", True, 30.0),
    ]:
        alert = system.on_event(action, active, t)
        if alert:
            print(f"[{t:>5.1f}s] ALERT {alert.level.name}: {alert.message}")
    alert = system.tick(34.5)
    if alert:
        print(f"[ 34.5s] ALERT {alert.level.name}: {alert.message}")
    system.on_event("eyes_off_road", False, 35.0)
    print()
    print(system.end_drive(120.0))
