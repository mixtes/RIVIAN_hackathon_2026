"""Clocks. The engine works in monotonic seconds; the wire protocol uses wall-clock UTC."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float:
        """Seconds from an arbitrary origin; never goes backwards. Passed to the engine."""
        ...

    def wall(self) -> datetime:
        """Current wall-clock time, timezone-aware UTC."""
        ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Deterministic clock for tests. Both clocks move together via `advance`."""

    def __init__(self, mono: float = 1000.0,
                 wall: datetime = datetime(2026, 11, 6, 8, 0, 0, tzinfo=UTC)) -> None:
        self._mono = mono
        self._wall = wall

    def monotonic(self) -> float:
        return self._mono

    def wall(self) -> datetime:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall += timedelta(seconds=seconds)
