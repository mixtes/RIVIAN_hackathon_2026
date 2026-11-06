"""ISO 3166-1 alpha-2 regions (spec §5.3)."""

from __future__ import annotations

from functools import lru_cache

import pycountry

GLOBAL = "GLOBAL"


@lru_cache
def _codes() -> dict[str, str]:
    return {c.alpha_2: getattr(c, "common_name", None) or c.name for c in pycountry.countries}


def is_valid_region(code: str) -> bool:
    return code in _codes()


def normalize_region(code: str) -> str:
    return code.strip().upper()


def all_regions() -> list[tuple[str, str]]:
    return sorted(_codes().items(), key=lambda kv: kv[1])
