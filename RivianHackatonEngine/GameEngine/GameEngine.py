"""
GameEngine - end-of-drive safety score.

Receives the same driver action events as AlertEngine, but produces no
real-time output. It records every dangerous action (how many times it
happened and how long it lasted) and, when the drive ends, returns a
DriveReport with a safety score.

Input  (per event): action name, is_active (True/False), timestamp in seconds.
                    CVEngine's per-frame state byte is turned into these events
                    by SafetySystem.on_frame() in main.py (CV_ENGINE_INTERFACE.md).
                    run_from_csv() replays an event CSV (event_id, action, is_active, timestamp).
Output (end of drive): DriveReport with score and per-action breakdown.

Scoring (from clip_score.py)
----------------------------
1. Flicker merge: occurrences of the same action less than MERGE_GAP_S apart
   are merged into one (the detector blinking on/off).
2. Overlap rule: at any moment only the most severe active action counts
   (PRIORITY order). An occurrence is penalized only for the seconds that no
   higher-priority action covers ("effective duration").
3. Penalty per occurrence: 0 below the grace period, else
   weight * (1 + (effective_duration - grace)) ** EXPONENT.
4. Score: 100 * 2 ** (-total / P50); total penalty P50 gives score 50.
   P50 was tuned for a ~30 s clip, so for drives longer than
   REFERENCE_DURATION_S the total is scaled by REFERENCE_DURATION_S / duration
   (penalty per 30 s of driving). Set REFERENCE_DURATION_S = None to disable.
5. ActionSummary.penalty = points lost for that action, its share of the total
   penalty times (100 - score), so the penalties add up to 100 - score.

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
from typing import Dict, List, Optional, Sequence, Tuple

TRUE_VALUES = {"1", "true", "t", "yes", "y"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ActionScoreConfig:
    """Penalty rules for a single dangerous action."""
    weight: float                  # how dangerous the action is ("w")
    grace_seconds: float = 0.0     # occurrences shorter than this are not penalized


MAX_SCORE = 100.0
MIN_SCORE = 0.0

P50 = 30.0                    # total penalty that gives score 50 (~10 s on the phone)
EXPONENT = 1.5                # penalty grows faster than linearly with duration
MERGE_GAP_S = 0.5             # merge the same action if the pause is shorter (flicker)
REFERENCE_DURATION_S: Optional[float] = 30.0   # P50 is "per 30 s clip"; None = no scaling

# Most severe first: a higher action "covers" lower ones while they overlap.
# Actions missing here come last (ordered by weight).
PRIORITY: Tuple[str, ...] = (
    "EYES_CLOSED",
    "PHONE_CALL",
    "PHONE_USE",
    "EYES_OFF_ROAD",
    "HANDS_OFF_WHEEL",
    "EATING_DRINKING",
    "SMOKING",
    "YAWN",
)


def normalize_action(action: str) -> str:
    """Canonical form of an action name: stripped and upper-case."""
    return action.strip().upper()


def load_events_from_csv(path: str) -> List[dict]:
    """Read events from a CSV with columns: event_id, action, is_active, timestamp.

    event_id is a group id: the is_active=True row that starts one occurrence
    of an action and the is_active=False row that ends it share the same
    event_id, so overlapping actions (e.g. a phone call during an
    eyes-off-road glance) can still be matched start-to-end by id.
    """
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
# PHONE_*, EYES_OFF_ROAD and HANDS_OFF_WHEEL come from clip_score.py
# (phone, gaze_away, hands_off). TODO: tune the others.
DEFAULT_SCORE_CONFIG: Dict[str, ActionScoreConfig] = {
    "PHONE_CALL": ActionScoreConfig(weight=1.0, grace_seconds=1.0),        # STUB: not detected yet
    "EYES_OFF_ROAD": ActionScoreConfig(weight=0.6, grace_seconds=2.0),
    "EYES_CLOSED": ActionScoreConfig(weight=1.5, grace_seconds=0.5),       # STUB: not detected yet
    "YAWN": ActionScoreConfig(weight=0.1, grace_seconds=0.0),              # STUB: not detected yet
    "HANDS_OFF_WHEEL": ActionScoreConfig(weight=0.3, grace_seconds=3.0),   # both hands off
    "PHONE_USE": ActionScoreConfig(weight=1.0, grace_seconds=1.0),         # STUB: not wired in CVEngine yet
    "EATING_DRINKING": ActionScoreConfig(weight=0.2, grace_seconds=3.0),   # STUB: not detected yet
    "SMOKING": ActionScoreConfig(weight=0.2, grace_seconds=3.0),           # STUB: not detected yet
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
    count: int = 0                 # penalized occurrences only
    total_duration: float = 0.0    # sum of their durations (s)
    longest_duration: float = 0.0
    penalty: float = 0.0           # points lost for this action (sum over actions = 100 - score)
    raw_penalty: float = 0.0       # sum of the occurrence penalties (before the score curve)


@dataclass
class DriveReport:
    """Output of the GameEngine at the end of a drive."""
    score: float
    drive_duration: float
    actions: Dict[str, ActionSummary]
    occurrences: List[Occurrence] = field(default_factory=list)
    total_penalty: float = 0.0     # sum of raw penalties, after duration scaling

    def __str__(self) -> str:
        lines = [f"Safety score: {self.score:.1f} / {MAX_SCORE:.0f}  "
                 f"(penalty {self.total_penalty:.2f})",
                 f"Drive duration: {self.drive_duration:.1f}s"]
        for s in self.actions.values():
            lines.append(f"  {s.action}: {s.count}x, total {s.total_duration:.1f}s, "
                         f"longest {s.longest_duration:.1f}s, -{s.penalty:.1f} pts")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interval helpers
# ---------------------------------------------------------------------------

def merge_intervals(intervals: Sequence[Tuple[float, float]],
                    gap: float = 0.0) -> List[Tuple[float, float]]:
    """Merge intervals that overlap or are less than `gap` apart."""
    out: List[List[float]] = []
    for s, e in sorted(intervals):
        if out and s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def covered_seconds(start: float, end: float,
                    cover: Sequence[Tuple[float, float]]) -> float:
    """Seconds of [start, end] covered by `cover` (merged, non-overlapping intervals)."""
    return sum(max(0.0, min(end, ce) - max(start, cs)) for cs, ce in cover)


def _key(occ: Occurrence) -> Tuple[str, float, float]:
    return (occ.action, occ.start_time, occ.end_time)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class GameEngine:
    def __init__(self, config: Optional[Dict[str, ActionScoreConfig]] = None):
        source = config if config is not None else DEFAULT_SCORE_CONFIG
        self.config = {normalize_action(a): c for a, c in source.items()}
        self.priority = ([a for a in PRIORITY if a in self.config]
                         + sorted((a for a in self.config if a not in PRIORITY),
                                  key=lambda a: -self.config[a].weight))
        self.p50 = P50
        self.exponent = EXPONENT
        self.merge_gap_s = MERGE_GAP_S
        self.reference_duration_s = REFERENCE_DURATION_S
        self._drive_start: Optional[float] = None
        self._active: Dict[str, float] = {}          # action -> start time
        self._occurrences: List[Occurrence] = []
        # Penalties of the last report, by occurrence (overlap rule needs context).
        self._penalties: Dict[Tuple[str, float, float], float] = {}

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
        """Penalty of one occurrence (> 0 means "penalized").

        For an occurrence from the last DriveReport this is the penalty used
        there (overlap rule applied). For any other occurrence it is scored on
        its own, by its full duration.
        """
        p = self._penalties.get(_key(occ))
        if p is not None:
            return p
        return self.occurrence_penalty(occ.action, occ.duration)

    def occurrence_penalty(self, action: str, effective_duration: float) -> float:
        """0 below the grace period, then weight * (1 + excess) ** EXPONENT."""
        cfg = self.config.get(normalize_action(action))
        if cfg is None:
            return 0.0
        excess = effective_duration - cfg.grace_seconds
        if excess < 0:
            return 0.0
        return cfg.weight * (1.0 + excess) ** self.exponent

    def penalty_to_score(self, total_penalty: float) -> float:
        score = MAX_SCORE * 2.0 ** (-total_penalty / self.p50)
        return max(MIN_SCORE, min(MAX_SCORE, score))

    def _merged_occurrences(self) -> List[Occurrence]:
        """Occurrences with flicker merged, sorted by start time."""
        out: List[Occurrence] = []
        for action in self.priority:
            spans = [(o.start_time, o.end_time) for o in self._occurrences if o.action == action]
            out += [Occurrence(action, s, e) for s, e in merge_intervals(spans, self.merge_gap_s)]
        return sorted(out, key=lambda o: (o.start_time, o.end_time, o.action))

    def _build_report(self, end_ts: float) -> DriveReport:
        occurrences = self._merged_occurrences()

        # Overlap rule: walk from the most severe action down; each action only
        # counts the time not already covered by a more severe one.
        self._penalties = {}
        covered: List[Tuple[float, float]] = []
        for action in self.priority:
            mine = [o for o in occurrences if o.action == action]
            for o in mine:
                effective = o.duration - covered_seconds(o.start_time, o.end_time, covered)
                self._penalties[_key(o)] = self.occurrence_penalty(action, effective)
            covered = merge_intervals(covered + [(o.start_time, o.end_time) for o in mine])

        summaries = {a: ActionSummary(a) for a in self.config}
        for o in occurrences:
            p = self._penalties[_key(o)]
            if p <= 0.0:
                continue
            s = summaries[o.action]
            s.count += 1
            s.total_duration += o.duration
            s.longest_duration = max(s.longest_duration, o.duration)
            s.raw_penalty += p

        start = end_ts if self._drive_start is None else self._drive_start
        drive_duration = max(0.0, end_ts - start)

        raw_total = sum(s.raw_penalty for s in summaries.values())
        scale = 1.0
        if self.reference_duration_s:
            scale = self.reference_duration_s / max(self.reference_duration_s, drive_duration)
        total = raw_total * scale
        score = self.penalty_to_score(total)

        lost = MAX_SCORE - score
        for s in summaries.values():
            s.penalty = lost * s.raw_penalty / raw_total if raw_total > 0 else 0.0

        return DriveReport(score, drive_duration, summaries, occurrences, total)

    @staticmethod
    def _now(timestamp: Optional[float]) -> float:
        return time.monotonic() if timestamp is None else timestamp


if __name__ == "__main__":
    csv_path = Path(__file__).parent / "test_inputs.csv"
    game = GameEngine()
    print(game.run_from_csv(str(csv_path)))
