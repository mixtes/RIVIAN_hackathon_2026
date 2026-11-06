from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.users import UserProfile

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{11,17}$")


def _vin(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().upper()
    if not v:
        return None
    if not _VIN_RE.match(v):
        raise ValueError("must be 11-17 characters, A-Z (no I, O, Q) and 0-9")
    return v


class CarRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="My Rivian", min_length=1, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    vin: str | None = None

    @field_validator("vin")
    @classmethod
    def _check_vin(cls, v: str | None) -> str | None:
        return _vin(v)


class CarRegisterResponse(BaseModel):
    car_id: uuid.UUID
    car_token: str
    name: str


class CarUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    vin: str | None = None

    @field_validator("vin")
    @classmethod
    def _check_vin(cls, v: str | None) -> str | None:
        return _vin(v)


class LinkedDriver(UserProfile):
    linked_at: datetime
    last_used_at: datetime | None


class CarInfo(BaseModel):
    car_id: uuid.UUID
    name: str
    model: str | None
    vin: str | None
    drivers: list[LinkedDriver]


class LinkDriverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LinkDriverResponse(BaseModel):
    user: UserProfile
    linked_at: datetime
