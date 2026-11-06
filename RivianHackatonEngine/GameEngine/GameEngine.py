"""
GameEngine - end-of-drive safety score.

Receives the same driver action events as AlertEngine, but produces no
real-time output. It records every dangerous action (how many times it
happened and how long it lasted) and, when the drive ends, returns a
DriveReport with a safety score.

Input  (per event): event_id, action name, is_active (True/False) timestamp in seconds or frames.
Output (end of drive): DriveReport with score and per-action breakdown.

Usage:
    game = GameEngine()
    game.start_drive(timestamp=0.0)
    game.process_event("PHONE_CALL", is_active=True, timestamp=10.0)
    game.process_event("PHONE_CALL", is_active=False, timestamp=15.0)
    report = game.end_drive(timestamp=30.0)
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

TRUE_VALUES = {"1", "true", "t", "yes", "y"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ActionScoreConfig:
    """Penalty rules for a single dangerous action."""
    penalty_per_occurrence: float      # flat points lost each time it happens
    penalty_per_second: float          # points lost for every second it lasts
    grace_seconds: float = 0.0         # occurrences shorter than this are ignored
    max_penalty_per_occurrence: Optional[float] = None  # cap for one occurrence


MAX_SCORE = 100.0
MIN_SCORE = 0.0

def normalize_action(action: str) -> str:
    """Canonical form of an action name: stripped and upper-case."""
    return action.strip().upper()


def load_events_from_csv(path: str) -> List[dict]:
    """Read events from a CSV with columns: event_id, action, is_active, timestamp."""
    events = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            events.append({
                "event_id": row["event_id"],
                "action": row["action"],
                "is_active": row["is_active"].strip().lower() in TRUE_VALUES,
                "timestamp": float(row["timestamp"]),
            })
    return events


# Keys are the canonical UPPER-case action names, identical to the
# backend/app mistake types (e.g. "PHONE_CALL").
# TODO: tune scores.
DEFAULT_SCORE_CONFIG: Dict[str, ActionScoreConfig] = {
    "PHONE_CALL": ActionScoreConfig(
        penalty_per_occurrence=1.0,
        penalty_per_second=0.5,
        grace_seconds=1.0,
        max_penalty_per_occurrence=10.0,
    ),
    "EYES_OFF_ROAD": ActionScoreConfig(
        penalty_per_occurrence=1.0,
        penalty_per_second=1.0,
        grace_seconds=0.5,
        max_penalty_per_occurrence=10.0,
    ),
    "EYES_CLOSED": ActionScoreConfig(
        penalty_per_occurrence=2.0,
        penalty_per_second=2.0,
        grace_seconds=0.5,
        max_penalty_per_occurrence=15.0,
    ),
    "YAWN": ActionScoreConfig(
        penalty_per_occurrence=0.5,
        penalty_per_second=0.0,
        grace_seconds=0.0,
        max_penalty_per_occurrence=0.5,
    ),
    "HANDS_OFF_WHEEL": ActionScoreConfig(
        penalty_per_occurrence=1.0,
        penalty_per_second=1.0,
        grace_seconds=0.5,
        max_penalty_per_occurrence=10.0,
    ),
    "PHONE_USE": ActionScoreConfig(
        penalty_per_occurrence=1.0,
        penalty_per_second=0.75,
        grace_seconds=1.0,
        max_penalty_per_occurrence=10.0,
    ),
    "EATING_DRINKING": ActionScoreConfig(
        penalty_per_occurrence=0.5,
        penalty_per_second=0.1,
        grace_seconds=2.0,
        max_penalty_per_occurrence=5.0,
    ),
    "SMOKING": ActionScoreConfig(
        penalty_per_occurrence=0.5,
        penalty_per_second=0.05,
        grace_seconds=2.0,
        max_penalty_per_occurrence=5.0,
    ),
}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Occurrence:
    """One continuous episode of a dangerous action."""
    action: str
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


@dataclass
class ActionSummary:
    action: str
    count: int = 0
    total_duration: float = 0.0
    longest_duration: float = 0.0
    penalty: float = 0.0


@dataclass
class DriveReport:
    """Output of the GameEngine at the end of a drive."""
    score: float
    drive_duration: float
    actions: Dict[str, ActionSummary]
    occurrences: List[Occurrence] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"Safety score: {self.score:.1f} / {MAX_SCORE:.0f}",
                 f"Drive duration: {self.drive_duration:.1f}s"]
        for s in self.actions.values():
            lines.append(f"  {s.action}: {s.count}x, total {s.total_duration:.1f}s, "
                         f"longest {s.longest_duration:.1f}s, -{s.penalty:.1f} pts")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class GameEngine:
    def __init__(self, config: Optional[Dict[str, ActionScoreConfig]] = None):
        source = config if config is not None else DEFAULT_SCORE_CONFIG
        self.config = {normalize_action(a): c for a, c in source.items()}
        self._drive_start: Optional[float] = None
        self._active: Dict[str, float] = {}          # action -> start time
        self._occurrences: List[Occurrence] = []

    # ---- public API -------------------------------------------------------

    def start_drive(self, timestamp: Optional[float] = None) -> None:
        self._drive_start = self._now(timestamp)
        self._active.clear()
        self._occurrences.clear()

    def run_from_csv(self, path: str) -> DriveReport:
        """Replay a full drive from a CSV of events and return the report."""
        events = load_events_from_csv(path)
        if not events:
            raise ValueError(f"No events found in {path}")
        self.start_drive(events[0]["timestamp"])
        for e in events:
            self.process_event(e["action"], e["is_active"], e["timestamp"])
        return self.end_drive(events[-1]["timestamp"])

    def process_event(self, action: str, is_active: bool,
                      timestamp: Optional[float] = None) -> None:
        """Record an event. Produces no output - results come from end_drive()."""
        ts = self._now(timestamp)
        action = normalize_action(action)
        if self._drive_start is None:
            self._drive_start = ts
        if action not in self.config:
            return

        if is_active:
            self._active.setdefault(action, ts)       # ignore repeated "still active"
        elif action in self._active:
            start = self._active.pop(action)
            self._occurrences.append(Occurrence(action, start, ts))

    def end_drive(self, timestamp: Optional[float] = None) -> DriveReport:
        """Close any open actions, compute the score and return the report."""
        ts = self._now(timestamp)
        for action, start in list(self._active.items()):
            self._occurrences.append(Occurrence(action, start, ts))
        self._active.clear()

        report = self._build_report(ts)
        self._drive_start = None
        return report

    # ---- scoring ----------------------------------------------------------

    def score_occurrence(self, occ: Occurrence) -> float:
        """Penalty for a single occurrence. TODO: tune / replace formula."""
        cfg = self.config[occ.action]
        if occ.duration < cfg.grace_seconds:
            return 0.0
        penalty = cfg.penalty_per_occurrence + cfg.penalty_per_second * occ.duration
        if cfg.max_penalty_per_occurrence is not None:
            penalty = min(penalty, cfg.max_penalty_per_occurrence)
        return penalty

    def _build_report(self, end_ts: float) -> DriveReport:
        summaries = {a: ActionSummary(a) for a in self.config}
        for occ in self._occurrences:
            p = self.score_occurrence(occ)
            if p == 0.0:
                continue
            s = summaries[occ.action]
            s.count += 1
            s.total_duration += occ.duration
            s.longest_duration = max(s.longest_duration, occ.duration)
            s.penalty += p

        total_penalty = sum(s.penalty for s in summaries.values())
        score = max(MIN_SCORE, min(MAX_SCORE, MAX_SCORE - total_penalty))
        start = end_ts if self._drive_start is None else self._drive_start
        drive_duration = end_ts - start
        return DriveReport(score, drive_duration, summaries, list(self._occurrences))

    @staticmethod
    def _now(timestamp: Optional[float]) -> float:
        return time.monotonic() if timestamp is None else timestamp


if __name__ == "__main__":
    csv_path = Path(__file__).parent / "test_inputs.csv"
    game = GameEngine()
    print(game.run_from_csv(str(csv_path)))
