from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.clock import FrozenClock
from app.db import make_sessionmaker
from scripts import seed as seed_mod
from tests.conftest import NOW, TEST_DATABASE_URL, make_settings


@pytest.fixture
def fast_seed_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # The seed reads get_settings(); point it at fast argon2 parameters.
    monkeypatch.setattr(seed_mod, "get_settings", lambda: make_settings())


async def test_seed_is_idempotent_and_populates_boards(
    fast_seed_settings: None, client: httpx.AsyncClient
) -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    maker = make_sessionmaker(engine)
    try:
        async with maker() as s:
            first = await seed_mod.seed(s, FrozenClock(NOW), drives_per_user=10)
        async with maker() as s:
            second = await seed_mod.seed(s, FrozenClock(NOW), drives_per_user=10)
    finally:
        await engine.dispose()
    assert first["users"] == second["users"] == 40
    assert first["drives"] == second["drives"]  # fixed random seed

    login = await client.post(
        "/api/v1/auth/login", json={"username": "demo", "password": "demo-password-123"}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
    for period in ("day", "week", "month", "year", "all"):
        lb = (await client.get(f"/api/v1/leaderboards?period={period}&region=GLOBAL", headers=headers)).json()
        assert lb["total_ranked"] > 0, period
    car_token = first["cars"][0][2]
    me = await client.get("/api/v1/cars/me", headers={"Authorization": f"Car {second['cars'][0][2]}"})
    assert me.status_code == 200 and len(me.json()["drivers"]) <= 8
    stale = await client.get("/api/v1/cars/me", headers={"Authorization": f"Car {car_token}"})
    assert stale.status_code == 401  # tokens rotate on every seed run
