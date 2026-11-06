"""Drive upload / history DTOs (spec §8.6, §8.7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domain.mistakes import is_valid_mistake_type

EARLIEST_DRIVE = datetime(2024, 1, 1, tzinfo=UTC)
MAX_DRIVE = timedelta(hours=24)
EVENT_GRACE = timedelta(minutes=1)
MAX_EVENTS = 5000
MAX_MISTAKE_TYPES = 200
# Engines round durations independently; allow a little slack against the drive duration.
DURATION_SLACK_S = 1.0


def _mistake_type(v: str) -> str:
    if not is_valid_mistake_type(v):
        raise ValueError("must match ^[A-Z][A-Z0-9_]{1,39}$")
    return v


MistakeType = Annotated[str, AfterValidator(_mistake_type)]


class MistakeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: MistakeType
    count: int = Field(ge=0, le=100_000)
    total_duration_s: float | None = Field(default=None, ge=0)
    max_duration_s: float | None = Field(default=None, ge=0)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: MistakeType
    started_at: AwareDatetime
    duration_s: float | None = Field(default=None, ge=0)


class DriveUpload(BaseModel):
    """The Game engine's `drive.result` payload plus `id` (= session_id).

    Unknown top-level fields are allowed and kept in `raw_payload`. The car app forwards the
    engine payload verbatim, so `session_id` and `user_id` may be present; if they are, they must
    agree with `id` and `X-Driver-Id` respectively.
    """

    model_config = ConfigDict(extra="allow")

    id: uuid.UUID
    started_at: AwareDatetime
    ended_at: AwareDatetime
    distance_km: float | None = Field(default=None, ge=0, le=2000)
    score: float = Field(ge=0, le=100)
    engine_version: str | None = Field(default=None, max_length=40)
    mistakes: list[MistakeIn] = Field(default_factory=list, max_length=MAX_MISTAKE_TYPES)
    events: list[EventIn] = Field(default_factory=list, max_length=MAX_EVENTS)
    extra: dict[str, Any] | None = None
    session_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None

    @property
    def duration(self) -> timedelta:
        return self.ended_at - self.started_at

    @model_validator(mode="after")
    def _consistency(self) -> DriveUpload:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be >= started_at")
        if self.duration > MAX_DRIVE:
            raise ValueError("drive duration must be <= 24 h")
        if self.started_at < EARLIEST_DRIVE:
            raise ValueError("started_at must be >= 2024-01-01")
        if self.session_id is not None and self.session_id != self.id:
            raise ValueError("session_id must equal id")

        seen: set[str] = set()
        limit = self.duration.total_seconds() + DURATION_SLACK_S
        for m in self.mistakes:
            if m.type in seen:
                raise ValueError(f"mistake type {m.type} appears more than once")
            seen.add(m.type)
            for name in ("total_duration_s", "max_duration_s"):
                value = getattr(m, name)
                if value is not None and value > limit:
                    raise ValueError(f"mistakes[{m.type}].{name} exceeds the drive duration")

        lo, hi = self.started_at, self.ended_at + EVENT_GRACE
        for i, e in enumerate(self.events):
            if not lo <= e.started_at <= hi:
                raise ValueError(f"events[{i}].started_at is outside the drive")
        return self


class CarRef(BaseModel):
    id: uuid.UUID
    name: str


class MistakesSummary(BaseModel):
    total_count: int
    types: list[str]


class DriveListItem(BaseModel):
    id: uuid.UUID
    car: CarRef
    started_at: datetime
    ended_at: datetime
    duration_s: int
    score: float
    mistakes_summary: MistakesSummary


class MistakeOut(BaseModel):
    type: str
    count: int
    total_duration_s: float | None
    max_duration_s: float | None


class EventOut(BaseModel):
    type: str
    started_at: datetime
    duration_s: float | None


class DriveDetail(DriveListItem):
    distance_km: float | None
    engine_version: str | None
    received_at: datetime
    mistakes: list[MistakeOut]
    events: list[EventOut]


class DrivePage(BaseModel):
    items: list[DriveListItem]
    next_cursor: str | None
