from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    # username is CITEXT, so equality is case-insensitive.
    stmt = select(User).where(User.username == username.strip())
    return (await session.execute(stmt)).scalar_one_or_none()


async def get(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def username_exists(session: AsyncSession, username: str) -> bool:
    stmt = select(func.count()).select_from(User).where(User.username == username)
    return bool((await session.execute(stmt)).scalar_one())


async def email_exists(session: AsyncSession, email: str) -> bool:
    stmt = select(func.count()).select_from(User).where(User.email == email)
    return bool((await session.execute(stmt)).scalar_one())


# ---- refresh tokens ---------------------------------------------------------


async def add_refresh_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    family_id: uuid.UUID,
    expires_at: datetime,
    created_at: datetime,
    user_agent: str | None,
) -> None:
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            expires_at=expires_at,
            created_at=created_at,
            user_agent=user_agent,
        )
    )


async def get_refresh_token_for_update(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def revoke_family(session: AsyncSession, family_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
