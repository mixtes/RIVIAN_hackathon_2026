"""Rolling time windows, series buckets and qualification thresholds (spec §5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.config import Settings


class Period(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    ALL = "all"


_WINDOW: dict[Period, timedelta | None] = {
    Period.DAY: timedelta(hours=24),
    Period.WEEK: timedelta(days=7),
    Period.MONTH: timedelta(days=30),
    Period.YEAR: timedelta(days=365),
    Period.ALL: None,
}

# date_trunc unit + generate_series step for the stats time series.
_BUCKET: dict[Period, tuple[str, str]] = {
    Period.DAY: ("hour", "1 hour"),
    Period.WEEK: ("day", "1 day"),
    Period.MONTH: ("day", "1 day"),
    Period.YEAR: ("month", "1 month"),
    Period.ALL: ("month", "1 month"),
}


@dataclass(frozen=True)
class Window:
    period: Period
    start: datetime | None  # None = unbounded (period=all, leaderboard)
    end: datetime


def window_for(period: Period, now: datetime) -> Window:
    """Rolling window ending at `now`. A drive belongs to it when start <= ended_at < end."""
    span = _WINDOW[period]
    return Window(period, None if span is None else now - span, now)


def bucket_unit(period: Period) -> str:
    return _BUCKET[period][0]


def bucket_step(period: Period) -> str:
    return _BUCKET[period][1]


def truncate(ts: datetime, unit: str) -> datetime:
    """Python twin of Postgres date_trunc(unit, ts, 'UTC'); used for the `all` stats window start."""
    ts = ts.astimezone(UTC)
    if unit == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    if unit == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "month":
        return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"unsupported unit {unit!r}")


def min_minutes(period: Period, settings: Settings) -> int:
    return {
        Period.DAY: settings.min_minutes_day,
        Period.WEEK: settings.min_minutes_week,
        Period.MONTH: settings.min_minutes_month,
        Period.YEAR: settings.min_minutes_year,
        Period.ALL: settings.min_minutes_all,
    }[period]
