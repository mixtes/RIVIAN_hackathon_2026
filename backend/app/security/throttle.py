"""Basic per-username failed-login throttle, in memory (spec §10). Proper rate limiting is future work."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from app.errors import TooManyAttempts


class LoginThrottle:
    def __init__(self, *, max_failures: int, window: timedelta, lockout: timedelta) -> None:
        self._max = max_failures
        self._window = window
        self._lockout = lockout
        self._failures: dict[str, deque[datetime]] = {}
        self._locked_until: dict[str, datetime] = {}

    @staticmethod
    def _key(username: str) -> str:
        return username.strip().lower()

    def check(self, username: str, now: datetime) -> None:
        key = self._key(username)
        until = self._locked_until.get(key)
        if until is not None:
            if now < until:
                retry = max(1, int((until - now).total_seconds()))
                raise TooManyAttempts(headers={"Retry-After": str(retry)})
            del self._locked_until[key]
            self._failures.pop(key, None)

    def record_failure(self, username: str, now: datetime) -> None:
        key = self._key(username)
        q = self._failures.setdefault(key, deque())
        q.append(now)
        while q and q[0] <= now - self._window:
            q.popleft()
        if len(q) >= self._max:
            self._locked_until[key] = now + self._lockout
            q.clear()

    def record_success(self, username: str) -> None:
        key = self._key(username)
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def reset(self) -> None:
        self._failures.clear()
        self._locked_until.clear()
