from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.schemas.common import AvatarColor, DisplayName, Password, RegionCode, Username


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str
    region_code: str
    avatar_color: str
    leaderboard_visible: bool
    created_at: datetime


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Username
    password: Password
    display_name: DisplayName
    region_code: RegionCode
    email: EmailStr | None = None
    avatar_color: AvatarColor = "#4F7DF3"

    @model_validator(mode="after")
    def _password_not_username(self) -> RegisterRequest:
        if self.password.strip().lower() == self.username:
            raise ValueError("password must not equal the username")
        return self


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    region_code: RegionCode | None = None
    avatar_color: AvatarColor | None = None
    leaderboard_visible: bool | None = None


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: Password


class LinkedCar(BaseModel):
    car_id: uuid.UUID
    name: str
    model: str | None
    linked_at: datetime
    last_used_at: datetime | None
