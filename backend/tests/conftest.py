"""Test fixtures. Tests run against a real PostgreSQL database (TEST_DATABASE_URL)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.clock import FrozenClock
from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://drive:drive@localhost:5432/drive_test"
)
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
PROVISIONING_KEY = "test-provisioning-key"
PASSWORD = "correct horse battery"

TABLES = "drive_events, drive_mistakes, drives, refresh_tokens, car_drivers, cars, users"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "jwt_secret": "test-secret-" + "x" * 40,
        "car_provisioning_key": PROVISIONING_KEY,
        "env": "test",
        # Fast argon2 for tests; production uses the argon2-cffi defaults.
        "argon2_time_cost": 1,
        "argon2_memory_cost_kib": 64,
        "argon2_parallelism": 1,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    """Run the real Alembic migrations once (downgrade + upgrade proves both directions work)."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
async def clean_db() -> AsyncIterator[None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    yield


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
async def app(clean_db: None, settings: Settings, clock: FrozenClock) -> AsyncIterator[Any]:
    application = create_app(settings, clock=clock)
    yield application
    await application.state.engine.dispose()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---- helpers -------------------------------------------------------------------


@dataclass
class CarCtx:
    id: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Car {self.token}"}

    def as_driver(self, user_id: str) -> dict[str, str]:
        return {"Authorization": f"Car {self.token}", "X-Driver-Id": user_id}


@dataclass
class UserCtx:
    id: str
    username: str
    access: str
    refresh: str
    region: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


class Api:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.c = client

    async def register_user(
        self, username: str, *, region: str = "RS", password: str = PASSWORD, **extra: Any
    ) -> UserCtx:
        body = {
            "username": username,
            "password": password,
            "display_name": username.title(),
            "region_code": region,
        }
        body.update(extra)
        r = await self.c.post("/api/v1/auth/register", json=body)
        assert r.status_code == 201, r.text
        data = r.json()
        return UserCtx(
            data["user"]["id"],
            username,
            data["tokens"]["access_token"],
            data["tokens"]["refresh_token"],
            region,
        )

    async def register_car(self, name: str = "Test R1S") -> CarCtx:
        r = await self.c.post(
            "/api/v1/cars/register",
            json={"name": name, "model": "R1S"},
            headers={"X-Provisioning-Key": PROVISIONING_KEY},
        )
        assert r.status_code == 201, r.text
        return CarCtx(r.json()["car_id"], r.json()["car_token"])

    async def link(self, car: CarCtx, username: str, password: str = PASSWORD) -> httpx.Response:
        return await self.c.post(
            "/api/v1/cars/me/drivers", json={"username": username, "password": password}, headers=car.headers
        )

    async def driver(self, car: CarCtx, username: str, *, region: str = "RS") -> UserCtx:
        """Register a user and link it to the car."""
        user = await self.register_user(username, region=region)
        r = await self.link(car, username)
        assert r.status_code == 201, r.text
        return user

    async def upload(self, car: CarCtx, user_id: str, body: dict[str, Any]) -> httpx.Response:
        return await self.c.post("/api/v1/drives", json=body, headers=car.as_driver(user_id))


def drive_body(
    *,
    ended_at: datetime = NOW - timedelta(hours=1),
    minutes: float = 30,
    score: float = 87.5,
    mistakes: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    drive_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    started = ended_at - timedelta(minutes=minutes)
    body: dict[str, Any] = {
        "id": drive_id or str(uuid.uuid4()),
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "ended_at": ended_at.isoformat().replace("+00:00", "Z"),
        "distance_km": None,
        "score": score,
        "engine_version": "test-engine/1.0",
        "mistakes": mistakes
        if mistakes is not None
        else [
            {"type": "EYES_OFF_ROAD", "count": 4, "total_duration_s": 9.6, "max_duration_s": 3.1},
            {"type": "YAWN", "count": 2, "total_duration_s": None, "max_duration_s": None},
        ],
        "events": events
        if events is not None
        else [
            {
                "type": "EYES_OFF_ROAD",
                "started_at": (started + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "duration_s": 3.1,
            }
        ],
        "extra": {"penalties": {"EYES_OFF_ROAD": 6.0}},
    }
    body.update(extra)
    return body


@pytest.fixture
def api(client: httpx.AsyncClient) -> Api:
    return Api(client)
