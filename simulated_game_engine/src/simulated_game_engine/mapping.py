"""Engine `DriveReport` -> wire `drive.result` payload (frontend spec §8.5)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .protocol import DriveResultOut, EventOut, MistakeOut, iso


def to_wall(t: float, *, wall_start: datetime, mono_start: float) -> datetime:
    """Engine (monotonic) time -> wall clock: wall_start + (t - mono_start)."""
    return wall_start + timedelta(seconds=t - mono_start)


def map_drive_report(
    report: Any,
    *,
    session_id: str,
    user_id: str,
    wall_start: datetime,
    mono_start: float,
    end_mono: float,
    engine_version: str,
    is_penalized: Callable[[Any], bool],
    end_reason: str,
) -> DriveResultOut:
    """Map a `GameEngine.DriveReport`.

    - `mistakes` lists every action the GameEngine is configured for (count may be 0),
      upper-cased, with count / total / longest duration of the *penalized* occurrences
      (that is what `ActionSummary` holds).
    - `events` are the occurrences that were penalized (above the grace period), so they
      stay consistent with `count`.
    - `extra.penalties` carries `ActionSummary.penalty` per type.
    """

    def wall(t: float) -> datetime:
        return to_wall(t, wall_start=wall_start, mono_start=mono_start)

    summaries = sorted(report.actions.values(), key=lambda s: s.action)
    mistakes = [
        MistakeOut(
            type=s.action.upper(),
            count=int(s.count),
            total_duration_s=round(float(s.total_duration), 2),
            max_duration_s=round(float(s.longest_duration), 2),
        )
        for s in summaries
    ]
    events = [
        EventOut(
            type=o.action.upper(),
            started_at=iso(wall(o.start_time)),
            duration_s=round(float(o.duration), 2),
        )
        for o in sorted(report.occurrences, key=lambda o: o.start_time)
        if is_penalized(o)
    ]
    return DriveResultOut(
        session_id=session_id,
        user_id=user_id,
        started_at=iso(wall_start),
        ended_at=iso(wall(end_mono)),
        distance_km=None,
        score=round(float(report.score), 2),
        engine_version=engine_version,
        mistakes=mistakes,
        events=events,
        extra={
            "penalties": {s.action.upper(): round(float(s.penalty), 2) for s in summaries},
            "end_reason": end_reason,
            "engine_drive_duration_s": round(float(report.drive_duration), 2),
        },
    )
