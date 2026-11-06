"""In-process TTL cache for leaderboard pages (spec §9.4). The `me` row is never cached."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.domain.regions import GLOBAL

CacheKey = tuple[str, str, int, int]  # (period, region, limit, offset)


@dataclass
class _Entry:
    value: Any
    expires_at: datetime


class LeaderboardCache:
    def __init__(self, ttl_s: int) -> None:
        self._ttl = timedelta(seconds=ttl_s)
        self._data: dict[CacheKey, _Entry] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._ttl.total_seconds() > 0

    def get(self, key: CacheKey, now: datetime) -> Any | None:
        if not self.enabled:
            return None
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._data[key]
                return None
            return entry.value

    def put(self, key: CacheKey, value: Any, now: datetime) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._data[key] = _Entry(value, now + self._ttl)

    def invalidate_region(self, region: str) -> None:
        """A new drive changes the user's region board and the global board."""
        with self._lock:
            for key in [k for k in self._data if k[1] in (region, GLOBAL)]:
                del self._data[key]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
