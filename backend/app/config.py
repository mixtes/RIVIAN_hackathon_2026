"""Application settings, read from environment variables and `.env` (spec §11)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://drive:drive@db:5432/drive"
    jwt_secret: str = Field(min_length=32)
    access_token_ttl_min: int = Field(default=15, gt=0)
    refresh_token_ttl_days: int = Field(default=30, gt=0)
    car_provisioning_key: str = Field(min_length=8)
    max_drivers_per_car: int = Field(default=8, gt=0)

    min_minutes_day: int = Field(default=10, ge=0)
    min_minutes_week: int = Field(default=45, ge=0)
    min_minutes_month: int = Field(default=120, ge=0)
    min_minutes_year: int = Field(default=480, ge=0)
    min_minutes_all: int = Field(default=480, ge=0)

    leaderboard_cache_ttl_s: int = Field(default=60, ge=0)
    cors_origins: str = ""
    log_level: str = "INFO"
    env: Literal["dev", "test", "prod"] = "dev"
    # In prod, /docs, /redoc and /openapi.json are only served when this flag is set.
    docs_enabled: bool = False

    # Argon2id parameters. Defaults are the argon2-cffi defaults (spec §7.3);
    # tests lower them to keep the suite fast.
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost_kib: int = Field(default=65536, ge=8)
    argon2_parallelism: int = Field(default=4, ge=1)

    # Basic brute-force protection (spec §10).
    login_max_failures: int = Field(default=10, ge=1)
    login_failure_window_min: int = Field(default=15, ge=1)
    login_lockout_min: int = Field(default=15, ge=1)

    max_body_bytes: int = Field(default=1_048_576, gt=0)

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def docs_visible(self) -> bool:
        return self.env != "prod" or self.docs_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required values (JWT_SECRET, CAR_PROVISIONING_KEY) come from the environment
