"""Opaque tokens (refresh, car): 32 random bytes, base64url; only the SHA-256 hex is stored (spec §7.2)."""

from __future__ import annotations

import hashlib
import secrets

CAR_TOKEN_PREFIX = "car_"


def generate_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def generate_car_token() -> str:
    return generate_token(CAR_TOKEN_PREFIX)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
