"""Users, auth (register/login/refresh/logout), profile, and password changes (spec §7, §8.4)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import LeaderboardCache
from app.clock import Clock
from app.config import Settings
from app.errors import (
    Conflict,
    InvalidCredentials,
    TokenExpired,
    TokenReused,
    Unauthenticated,
    ValidationFailed,
)
from app.models import User
from app.repositories import users as users_repo
from app.schemas.auth import TokenPair
from app.schemas.users import ChangePasswordRequest, RegisterRequest, UpdateProfileRequest
from app.security.jwt import encode_access_token
from app.security.passwords import PasswordService
from app.security.throttle import LoginThrottle
from app.security.tokens import generate_token, hash_token


class UsernameTaken(Conflict):
    code = "USERNAME_TAKEN"
    message = "This username is taken"


class EmailTaken(Conflict):
    code = "EMAIL_TAKEN"
    message = "This email is already registered"


@dataclass
class AccountService:
    session: AsyncSession
    settings: Settings
    clock: Clock
    passwords: PasswordService
    throttle: LoginThrottle

    # ---- users -----------------------------------------------------------------

    async def ensure_available(self, req: RegisterRequest) -> None:
        if await users_repo.username_exists(self.session, req.username):
            raise UsernameTaken(details=[{"field": "username", "issue": "taken"}])
        if req.email and await users_repo.email_exists(self.session, req.email):
            raise EmailTaken(details=[{"field": "email", "issue": "taken"}])

    async def create_user(self, req: RegisterRequest) -> User:
        """Insert a user (not committed). Raises USERNAME_TAKEN / EMAIL_TAKEN."""
        await self.ensure_available(req)
        now = self.clock.now()
        user = User(
            username=req.username,
            email=req.email,
            password_hash=await self.passwords.hash(req.password),
            display_name=req.display_name,
            region_code=req.region_code,
            avatar_color=req.avatar_color,
            leaderboard_visible=True,
            created_at=now,
            updated_at=now,
            last_login_at=now,
        )
        self.session.add(user)
        try:
            await self.session.flush()
        except IntegrityError as exc:  # lost a race with a concurrent registration
            await self.session.rollback()
            if "email" in str(exc.orig):
                raise EmailTaken(details=[{"field": "email", "issue": "taken"}]) from exc
            raise UsernameTaken(details=[{"field": "username", "issue": "taken"}]) from exc
        return user

    async def authenticate(self, username: str, password: str) -> User:
        """Check credentials with the brute-force throttle. Same error for unknown user and bad password."""
        now = self.clock.now()
        self.throttle.check(username, now)
        user = await users_repo.get_by_username(self.session, username)
        ok = await self.passwords.verify(user.password_hash if user else None, password)
        if user is None or not ok:
            self.throttle.record_failure(username, now)
            raise InvalidCredentials()
        self.throttle.record_success(username)
        if self.passwords.needs_rehash(user.password_hash):
            user.password_hash = await self.passwords.hash(password)
        user.last_login_at = now
        return user

    # ---- tokens ----------------------------------------------------------------

    async def issue_tokens(
        self, user: User, *, family_id: uuid.UUID | None = None, user_agent: str | None = None
    ) -> TokenPair:
        now = self.clock.now()
        access = encode_access_token(
            user.id, secret=self.settings.jwt_secret, ttl_min=self.settings.access_token_ttl_min, now=now
        )
        refresh = generate_token()
        await users_repo.add_refresh_token(
            self.session,
            user_id=user.id,
            token_hash=hash_token(refresh),
            family_id=family_id or uuid.uuid4(),
            expires_at=now + timedelta(days=self.settings.refresh_token_ttl_days),
            created_at=now,
            user_agent=(user_agent or "")[:500] or None,
        )
        return TokenPair(access_token=access.token, expires_in=access.expires_in, refresh_token=refresh)

    async def register(self, req: RegisterRequest, user_agent: str | None) -> tuple[User, TokenPair]:
        user = await self.create_user(req)
        tokens = await self.issue_tokens(user, user_agent=user_agent)
        await self.session.commit()
        return user, tokens

    async def login(self, username: str, password: str, user_agent: str | None) -> tuple[User, TokenPair]:
        user = await self.authenticate(username, password)
        tokens = await self.issue_tokens(user, user_agent=user_agent)
        await self.session.commit()
        return user, tokens

    async def refresh(self, refresh_token: str, user_agent: str | None) -> TokenPair:
        now = self.clock.now()
        row = await users_repo.get_refresh_token_for_update(self.session, hash_token(refresh_token))
        if row is None:
            raise Unauthenticated("Invalid refresh token")
        if row.revoked_at is not None:
            # Reuse of a rotated token: assume theft, kill the whole family.
            await users_repo.revoke_family(self.session, row.family_id, now)
            await self.session.commit()
            raise TokenReused()
        if row.expires_at <= now:
            raise TokenExpired("Refresh token expired")
        user = await users_repo.get(self.session, row.user_id)
        if user is None:  # pragma: no cover - cascade delete
            raise Unauthenticated("Invalid refresh token")
        row.revoked_at = now
        tokens = await self.issue_tokens(user, family_id=row.family_id, user_agent=user_agent)
        await self.session.commit()
        return tokens

    async def logout(self, user: User, refresh_token: str) -> None:
        row = await users_repo.get_refresh_token_for_update(self.session, hash_token(refresh_token))
        if row is not None and row.user_id == user.id:
            await users_repo.revoke_family(self.session, row.family_id, self.clock.now())
            await self.session.commit()

    # ---- profile ---------------------------------------------------------------

    async def update_profile(self, user: User, req: UpdateProfileRequest, cache: LeaderboardCache) -> User:
        changes = req.model_dump(exclude_unset=True)
        nulls = [k for k, v in changes.items() if v is None]
        if nulls:
            raise ValidationFailed(details=[{"field": k, "issue": "must not be null"} for k in nulls])
        for key, value in changes.items():
            setattr(user, key, value)
        if changes:
            user.updated_at = self.clock.now()
            await self.session.commit()
            cache.clear()  # region / visibility / display data feed every board
        return user

    async def change_password(self, user: User, req: ChangePasswordRequest) -> None:
        if not await self.passwords.verify(user.password_hash, req.current_password):
            raise InvalidCredentials("Current password is wrong")
        if req.new_password.strip().lower() == str(user.username).lower():
            raise ValidationFailed(
                details=[{"field": "new_password", "issue": "must not equal the username"}]
            )
        now = self.clock.now()
        user.password_hash = await self.passwords.hash(req.new_password)
        user.updated_at = now
        await users_repo.revoke_all_for_user(self.session, user.id, now)
        await self.session.commit()
