"""Mistake type registry (spec §5.2). Types are free strings; this only provides display metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass

MISTAKE_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")


@dataclass(frozen=True)
class MistakeTypeInfo:
    type: str
    label: str
    has_duration: bool


KNOWN_MISTAKE_TYPES: tuple[MistakeTypeInfo, ...] = (
    MistakeTypeInfo("EYES_OFF_ROAD", "Eyes off road", True),
    MistakeTypeInfo("PHONE_USE", "Phone use", True),
    MistakeTypeInfo("EYES_CLOSED", "Eyes closed", True),
    MistakeTypeInfo("YAWN", "Yawn", False),
    MistakeTypeInfo("HANDS_OFF_WHEEL", "Hands off wheel", True),
    MistakeTypeInfo("PHONE_CALL", "Phone call", True),
    MistakeTypeInfo("EATING_DRINKING", "Eating / drinking", True),
    MistakeTypeInfo("SMOKING", "Smoking", True),
)

KNOWN_TYPES = frozenset(m.type for m in KNOWN_MISTAKE_TYPES)


def is_valid_mistake_type(value: str) -> bool:
    return bool(MISTAKE_TYPE_RE.match(value))
