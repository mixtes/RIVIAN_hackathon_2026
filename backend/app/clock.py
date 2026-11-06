"""Injectable clock so server logic (and tests) never call datetime.now() directly (spec §10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A clock that only moves when told to. Used by tests and the seed script."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        self._now = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, at: datetime) -> None:
        self._now = at.astimezone(UTC)

    def advance(self, **kwargs: float) -> None:
        self._now = self._now + timedelta(**kwargs)
