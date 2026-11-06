"""
GameEngine - end-of-drive safety score.

Receives the same driver action events as AlertEngine, but produces no
real-time output. It records every dangerous action (how many times it
happened and how long it lasted) and, when the drive ends, returns a
DriveReport with a safety score.

Input  (per event): action name, is_active (started / still going) or ended,
                    timestamp in seconds.
Output (end of drive): DriveReport with score and per-action breakdown.

Usage:
    game = GameEngine()
    game.start_drive(timestamp=0.0)
    game.process_event("phone_call", is_active=True, timestamp=10.0)
    game.process_event("phone_call", is_active=False, timestamp=15.0)
    report = game.end_drive(timestamp=600.0)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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

# TODO: define real actions and their scores.
DEFAULT_SCORE_CONFIG: Dict[str, ActionScoreConfig] = {
    "phone_call": ActionScoreConfig(
        penalty_per_occurrence=5.0,
        penalty_per_second=0.5,
        grace_seconds=1.0,
        max_penalty_per_occurrence=20.0,
    ),
    "eyes_off_road": ActionScoreConfig(
        penalty_per_occurrence=3.0,
        penalty_per_second=1.0,
        grace_seconds=0.5,
        max_penalty_per_occurrence=15.0,
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
        self.config = config if config is not None else DEFAULT_SCORE_CONFIG
        self._drive_start: Optional[float] = None
        self._active: Dict[str, float] = {}          # action -> start time
        self._occurrences: List[Occurrence] = []

    # ---- public API -------------------------------------------------------

    def start_drive(self, timestamp: Optional[float] = None) -> None:
        self._drive_start = self._now(timestamp)
        self._active.clear()
        self._occurrences.clear()

    def process_event(self, action: str, is_active: bool,
                      timestamp: Optional[float] = None) -> None:
        """Record an event. Produces no output - results come from end_drive()."""
        ts = self._now(timestamp)
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
    game = GameEngine()
    game.start_drive(0.0)
    game.process_event("phone_call", True, 10.0)
    game.process_event("phone_call", False, 18.0)
    game.process_event("eyes_off_road", True, 30.0)
    game.process_event("eyes_off_road", False, 30.3)   # under grace period
    game.process_event("eyes_off_road", True, 45.0)
    game.process_event("eyes_off_road", False, 48.0)
    game.process_event("phone_call", True, 100.0)      # still active at end
    print(game.end_drive(120.0))
