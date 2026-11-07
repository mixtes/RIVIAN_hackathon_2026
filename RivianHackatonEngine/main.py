"""
Feeds the same driver-action events to both engines in parallel:
  - AlertEngine returns alert updates immediately (real time)
  - GameEngine only records; its score comes out at end_drive()

Two input paths, both end up in on_event():
  - on_event(action, is_active, ts)   events (simulator, tests)
  - on_frame(ts, flags)               CVEngine: one state byte per analyzed frame

Contracts: AlertEngine/CAR_FRONTEND_INTERFACE.md, GameEngine/CAR_FRONTEND_INTERFACE.md,
           AlertEngine/CV_ENGINE_INTERFACE.md (= GameEngine/CV_ENGINE_INTERFACE.md, sections 2-5).

Replay CVEngine output (offline, or piped live):
    python main.py --cv flags.csv                      # from live.py --flags flags.csv
    python CVEngine/live.py VIDEO --stream | python main.py --cv -
"""

from __future__ import annotations

import math
import sys
from typing import Iterable, Iterator, List, Optional, TextIO, Tuple

from AlertEngine.AlertEngine import (Alert, AlertClear, AlertEngine, AlertLevel,
                                     AlertUpdate)
from GameEngine.GameEngine import DriveReport, GameEngine

__version__ = "0.4.0"

# The CVEngine byte (CV_ENGINE_INTERFACE.md section 3), highest bit (= priority) first.
CV_FLAG_ACTIONS: Tuple[Tuple[int, str], ...] = (
    (0x80, "EYES_CLOSED"),       # fell asleep: eyes closed or slumped (eyes_closed.py, slump.py)
    (0x40, "EYES_OFF_ROAD"),     # looked off the road
    (0x20, "HANDS_OFF_WHEEL"),   # hands off the steering wheel
    (0x10, "PHONE_USE"),         # phone at the ear or in hand (live_phone.PhoneMonitor)
    (0x08, "YAWN"),              # yawning (yawn.py)
)
CV_FLAGS_MASK = 0xF8             # the low 3 bits are reserved and ignored

# Gap rule (CV_ENGINE_INTERFACE.md section 8.1): if no record arrives for longer
# than this, the CV stream counts as interrupted and every open action is closed
# at the last record's timestamp, so an outage is never alerted or scored as a
# violation. Matches CVEngine's own observation-loss time (live.py LOSS_S).
# Pass gap_seconds=None to SafetySystem to turn it off.
CV_GAP_SECONDS = 2.0

# The subset of the 8 canonical actions that real detection (CVEngine) emits today:
# live.py sets bit 0x80 (asleep), 0x40 (eyes_off_road.py), 0x20 (hands off the wheel), 0x10 (phone use) and 0x08 (yawn).
DETECTED_ACTIONS = ("EYES_CLOSED", "EYES_OFF_ROAD", "HANDS_OFF_WHEEL", "PHONE_USE", "YAWN")

__all__ = ["SafetySystem", "Alert", "AlertClear", "AlertLevel", "AlertUpdate",
           "DriveReport", "DETECTED_ACTIONS", "CV_FLAG_ACTIONS", "CV_FLAGS_MASK",
           "CV_GAP_SECONDS", "read_cv_records", "replay_cv", "__version__"]


def _is_number(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class SafetySystem:
    def __init__(self, gap_seconds: Optional[float] = CV_GAP_SECONDS) -> None:
        self.alerts = AlertEngine()
        self.game = GameEngine()
        self.gap_seconds = gap_seconds
        self._prev_flags = 0                     # CV byte of the last accepted record
        self._prev_ts: Optional[float] = None    # timestamp of the last accepted record

    def start_drive(self, timestamp: Optional[float] = None) -> None:
        self.alerts.reset()
        self._reset_cv()
        self.game.start_drive(timestamp)

    def on_event(self, action: str, is_active: bool,
                 timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Feeds both engines; returns the AlertEngine's updates."""
        self.game.process_event(action, is_active, timestamp)
        return self.alerts.process_event(action, is_active, timestamp)

    def on_frame(self, timestamp: float, flags: int) -> List[AlertUpdate]:
        """One analyzed frame from CVEngine. Returns the AlertEngine's updates, like on_event.

        Bit changes become on_event calls (0 -> 1 = start, 1 -> 0 = end): first all
        ends, then all starts, highest bit first. Unchanged bits make no call.
        Invalid records (flags not an int 0..255, timestamp not finite, or older
        than the previous record) are ignored and return [].
        """
        if not isinstance(flags, int) or isinstance(flags, bool) or not 0 <= flags <= 255:
            return []
        if not _is_number(timestamp):
            return []
        ts = float(timestamp)
        if self._prev_ts is not None and ts < self._prev_ts:
            return []

        out: List[AlertUpdate] = []
        if (self.gap_seconds is not None and self._prev_ts is not None
                and ts - self._prev_ts > self.gap_seconds and self._prev_flags):
            # Stream was interrupted: close what was open when it was last seen.
            for bit, action in CV_FLAG_ACTIONS:
                if self._prev_flags & bit:
                    out += self.on_event(action, False, self._prev_ts)
            self._prev_flags = 0

        flags &= CV_FLAGS_MASK
        changed = flags ^ self._prev_flags
        for bit, action in CV_FLAG_ACTIONS:          # ends first
            if changed & bit and not flags & bit:
                out += self.on_event(action, False, ts)
        for bit, action in CV_FLAG_ACTIONS:          # then starts
            if changed & bit and flags & bit:
                out += self.on_event(action, True, ts)

        self._prev_flags = flags
        self._prev_ts = ts
        return out

    def tick_all(self, timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Escalations of all active actions since the last call."""
        return self.alerts.tick_all(timestamp)

    def dismiss(self, alert_id: str, timestamp: Optional[float] = None) -> List[AlertUpdate]:
        """Driver dismissed a latched alert. AlertEngine only: never changes the score."""
        return self.alerts.dismiss(alert_id, timestamp)

    def end_drive(self, timestamp: Optional[float] = None) -> DriveReport:
        self.alerts.reset()
        self._reset_cv()
        return self.game.end_drive(timestamp)

    def _reset_cv(self) -> None:
        self._prev_flags = 0
        self._prev_ts = None


# ---------------------------------------------------------------------------
# Replaying CVEngine output - dev / testing only
# ---------------------------------------------------------------------------

def read_cv_records(lines: Iterable[str]) -> Iterator[Tuple[float, int]]:
    """Parse CVEngine output into (timestamp, flags) records.

    Accepts both formats live.py writes:
      --stream       "timestamp,flags" per line              e.g. 12.346,32
      --flags FILE   CSV "frame,time_s,flags,hands_off,eyes_off_road" (header required)
    Blank lines, comments (#) and unparsable lines are skipped.
    """
    ts_col, flags_col = 0, 1
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cells = [c.strip() for c in line.split(",")]
        if "time_s" in cells and "flags" in cells:    # header of a --flags CSV
            ts_col, flags_col = cells.index("time_s"), cells.index("flags")
            continue
        try:
            yield float(cells[ts_col]), int(cells[flags_col])
        except (IndexError, ValueError):
            continue


def replay_cv(records: Iterable[Tuple[float, int]], system: Optional[SafetySystem] = None,
              tick: float = 0.1, out: Optional[TextIO] = None) -> DriveReport:
    """Run a whole drive from CV records, ticking every `tick` s like the bridge.

    Prints alert updates as they happen (to `out`, default stdout) and returns the report.
    """
    system = system or SafetySystem()
    out = out or sys.stdout
    started = False
    first = last = 0.0
    n = 1                                     # next tick = first + n * tick (no float drift)
    for ts, flags in records:
        if not started:
            system.start_drive(ts)
            started, first, last = True, ts, ts
        while round(first + n * tick, 6) <= ts:
            t = round(first + n * tick, 6)
            _show(t, system.tick_all(t), out)
            n += 1
        _show(ts, system.on_frame(ts, flags), out)
        last = max(last, ts)
    if not started:
        system.start_drive(0.0)
    return system.end_drive(last)


def _show(t: float, updates: List[AlertUpdate], out: Optional[TextIO] = None) -> None:
    out = out or sys.stdout
    for u in updates:
        if isinstance(u, Alert):
            ack = " [needs dismiss]" if u.requires_ack else ""
            print(f"[{t:>6.2f}s] ALERT {u.action} {u.level.name}: {u.message}{ack}",
                  file=out, flush=True)
        else:
            print(f"[{t:>6.2f}s] CLEAR {u.action} ({u.reason}, was {u.last_level.name})",
                  file=out, flush=True)


def _demo() -> None:
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


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--cv":
        src = sys.argv[2]
        if src == "-":
            report = replay_cv(read_cv_records(sys.stdin))
        else:
            with open(src, encoding="utf-8") as f:
                report = replay_cv(read_cv_records(f))
        print()
        print(report)
    elif len(sys.argv) == 1:
        _demo()
    else:
        sys.exit("usage: python main.py [--cv FILE | --cv -]")
