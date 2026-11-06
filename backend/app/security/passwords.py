"""Argon2id password hashing (spec §7.3). Hashing runs in a worker thread (it is CPU/memory heavy)."""

from __future__ import annotations

from anyio import to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import Settings


class PasswordService:
    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
        )
        # Used to spend the same time on unknown usernames as on wrong passwords.
        self._dummy_hash = self._hasher.hash("dummy-password-for-timing")

    def hash_sync(self, password: str) -> str:
        return self._hasher.hash(password)

    async def hash(self, password: str) -> str:
        return await to_thread.run_sync(self._hasher.hash, password)

    async def verify(self, password_hash: str | None, password: str) -> bool:
        target = password_hash or self._dummy_hash

        def _verify() -> bool:
            try:
                return self._hasher.verify(target, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False

        ok = await to_thread.run_sync(_verify)
        return ok and password_hash is not None

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
