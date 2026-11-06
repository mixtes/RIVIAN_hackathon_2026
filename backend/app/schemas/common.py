"""Reusable field validators shared by request schemas."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator

from app.domain.regions import is_valid_region, normalize_region

USERNAME_RE = re.compile(r"^[a-z0-9_.]+$")
RESERVED_USERNAMES = frozenset({"admin", "root", "system", "me", "global"})
AVATAR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _username(v: str) -> str:
    v = v.strip().lower()
    if not 3 <= len(v) <= 32:
        raise ValueError("must be 3-32 characters")
    if not USERNAME_RE.match(v):
        raise ValueError("may only contain a-z, 0-9, '_' and '.'")
    if v in RESERVED_USERNAMES:
        raise ValueError("this username is reserved")
    return v


def _password(v: str) -> str:
    if not 8 <= len(v) <= 128:
        raise ValueError("must be 8-128 characters")
    return v


def _display_name(v: str) -> str:
    v = v.strip()
    if not 1 <= len(v) <= 40:
        raise ValueError("must be 1-40 characters")
    return v


def _region(v: object) -> object:
    if isinstance(v, str):
        code = normalize_region(v)
        if not is_valid_region(code):
            raise ValueError("must be an ISO 3166-1 alpha-2 country code")
        return code
    return v


def _avatar(v: str) -> str:
    if not AVATAR_RE.match(v):
        raise ValueError("must be a hex color like #4F7DF3")
    return v.upper()


Username = Annotated[str, AfterValidator(_username)]
Password = Annotated[str, AfterValidator(_password)]
DisplayName = Annotated[str, AfterValidator(_display_name)]
RegionCode = Annotated[str, BeforeValidator(_region)]
AvatarColor = Annotated[str, AfterValidator(_avatar)]
