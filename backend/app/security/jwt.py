"""HS256 access tokens for user principals (spec §7.2)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from app.errors import TokenExpired, Unauthenticated

ALGORITHM = "HS256"


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_in: int


def encode_access_token(user_id: uuid.UUID, *, secret: str, ttl_min: int, now: datetime) -> AccessToken:
    exp = now + timedelta(minutes=ttl_min)
    claims = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return AccessToken(jwt.encode(claims, secret, algorithm=ALGORITHM), ttl_min * 60)


def decode_access_token(token: str, *, secret: str, now: datetime) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp", "iat", "typ"], "verify_exp": False},
        )
    except jwt.PyJWTError as exc:
        raise Unauthenticated("Invalid access token") from exc
    # Expiry is checked against the injectable clock, not the wall clock.
    if int(claims["exp"]) <= int(now.timestamp()):
        raise TokenExpired()
    if claims.get("typ") != "access":
        raise Unauthenticated("Invalid access token")
    try:
        return uuid.UUID(str(claims["sub"]))
    except ValueError as exc:
        raise Unauthenticated("Invalid access token") from exc
