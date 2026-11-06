from __future__ import annotations

import logging
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import PASSWORD, PROVISIONING_KEY, TEST_DATABASE_URL, Api


async def test_register_car_requires_provisioning_key(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/cars/register", json={"name": "x"})
    assert r.status_code == 401
    r = await client.post(
        "/api/v1/cars/register", json={"name": "x"}, headers={"X-Provisioning-Key": "wrong"}
    )
    assert r.status_code == 401


async def test_register_and_get_car(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car("Hackathon R1S")
    assert car.token.startswith("car_")
    r = await client.get("/api/v1/cars/me", headers=car.headers)
    assert r.status_code == 200
    assert r.json() == {"car_id": car.id, "name": "Hackathon R1S", "model": "R1S", "vin": None, "drivers": []}


async def test_patch_car_and_vin_uniqueness(api: Api, client: httpx.AsyncClient) -> None:
    car1 = await api.register_car()
    car2 = await api.register_car()
    r = await client.patch(
        "/api/v1/cars/me", json={"name": "Family", "vin": "7pdsgabl1nn000001"}, headers=car1.headers
    )
    assert r.status_code == 200 and r.json()["vin"] == "7PDSGABL1NN000001" and r.json()["name"] == "Family"
    dup = await client.patch("/api/v1/cars/me", json={"vin": "7PDSGABL1NN000001"}, headers=car2.headers)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "VIN_TAKEN"
    bad = await client.patch("/api/v1/cars/me", json={"vin": "IOQ"}, headers=car2.headers)
    assert bad.status_code == 422


async def test_car_token_errors(client: httpx.AsyncClient, api: Api) -> None:
    r = await client.get("/api/v1/cars/me", headers={"Authorization": "Car car_bogus"})
    assert r.status_code == 401
    user = await api.register_user("mixa")
    r = await client.get("/api/v1/cars/me", headers=user.headers)  # a user JWT isn't a car token
    assert r.status_code == 403


async def test_link_driver_201_then_200(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car()
    user = await api.register_user("mixa")
    first = await api.link(car, "MIXA")
    assert first.status_code == 201 and first.json()["user"]["id"] == user.id
    again = await api.link(car, "mixa")
    assert again.status_code == 200
    info = (await client.get("/api/v1/cars/me", headers=car.headers)).json()
    assert [d["username"] for d in info["drivers"]] == ["mixa"]
    assert info["drivers"][0]["linked_at"]


async def test_link_wrong_credentials(api: Api) -> None:
    car = await api.register_car()
    await api.register_user("mixa")
    r = await api.link(car, "mixa", "wrong password")
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = await api.link(car, "ghost", "wrong password")
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_driver_limit(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car()
    for i in range(8):
        await api.driver(car, f"driver{i}")
    await api.register_user("ninth")
    r = await api.link(car, "ninth")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CAR_DRIVER_LIMIT"

    # Creating an account on a full car fails BEFORE the user is created.
    r = await client.post(
        "/api/v1/cars/me/drivers/register",
        json={"username": "tenth", "password": PASSWORD, "display_name": "T", "region_code": "RS"},
        headers=car.headers,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "CAR_DRIVER_LIMIT"
    login = await client.post("/api/v1/auth/login", json={"username": "tenth", "password": PASSWORD})
    assert login.status_code == 401

    # Re-linking an already-active driver doesn't count against the limit.
    assert (await api.link(car, "driver0")).status_code == 200


async def test_register_driver_on_car(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car()
    r = await client.post(
        "/api/v1/cars/me/drivers/register",
        json={
            "username": "newbie",
            "password": PASSWORD,
            "display_name": "New",
            "region_code": "US",
            "avatar_color": "#E8603C",
        },
        headers=car.headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"user", "linked_at"}  # no JWTs for the car
    user_id = body["user"]["id"]
    me = await client.get("/api/v1/users/me", headers=car.as_driver(user_id))
    assert me.status_code == 200 and me.json()["username"] == "newbie"
    # It's a normal account: phone-app login works.
    login = await client.post("/api/v1/auth/login", json={"username": "newbie", "password": PASSWORD})
    assert login.status_code == 200

    dup = await client.post(
        "/api/v1/cars/me/drivers/register",
        json={"username": "NEWBIE", "password": PASSWORD, "display_name": "x", "region_code": "US"},
        headers=car.headers,
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "USERNAME_TAKEN"


async def test_unlink_and_relink(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car()
    user = await api.driver(car, "mixa")
    assert (await client.get("/api/v1/users/me", headers=car.as_driver(user.id))).status_code == 200

    r = await client.delete(f"/api/v1/cars/me/drivers/{user.id}", headers=car.headers)
    assert r.status_code == 204
    r = await client.delete(f"/api/v1/cars/me/drivers/{user.id}", headers=car.headers)
    assert r.status_code == 204  # idempotent for queued offline unlinks
    r = await client.get("/api/v1/users/me", headers=car.as_driver(user.id))
    assert r.status_code == 403 and r.json()["error"]["code"] == "DRIVER_NOT_LINKED"
    r = await client.delete(f"/api/v1/cars/me/drivers/{uuid.uuid4()}", headers=car.headers)
    assert r.status_code == 404

    # Signing in again re-activates the link.
    assert (await api.link(car, "mixa")).status_code == 200
    assert (await client.get("/api/v1/users/me", headers=car.as_driver(user.id))).status_code == 200


async def test_driver_header_errors(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car()
    other = await api.register_user("stranger")
    r = await client.get("/api/v1/users/me", headers=car.headers)
    assert r.status_code == 400
    r = await client.get("/api/v1/users/me", headers={**car.headers, "X-Driver-Id": "not-a-uuid"})
    assert r.status_code == 400
    r = await client.get("/api/v1/users/me", headers=car.as_driver(other.id))
    assert r.status_code == 403 and r.json()["error"]["code"] == "DRIVER_NOT_LINKED"


async def test_user_side_car_management(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car("Family R1S")
    user = await api.driver(car, "mixa")
    cars = (await client.get("/api/v1/users/me/cars", headers=user.headers)).json()
    assert [c["name"] for c in cars] == ["Family R1S"]
    r = await client.delete(f"/api/v1/users/me/cars/{car.id}", headers=user.headers)
    assert r.status_code == 204
    assert (await client.get("/api/v1/users/me/cars", headers=user.headers)).json() == []
    r = await client.get("/api/v1/users/me", headers=car.as_driver(user.id))
    assert r.status_code == 403
    # Car tokens can't use user-only endpoints.
    r = await client.get("/api/v1/users/me/cars", headers=car.headers)
    assert r.status_code == 403


async def test_revoked_car(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car()
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE cars SET revoked_at = now() WHERE id = :id"), {"id": car.id})
    await engine.dispose()
    r = await client.get("/api/v1/cars/me", headers=car.headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CAR_REVOKED"


async def test_link_brute_force(api: Api) -> None:
    car = await api.register_car()
    await api.register_user("mixa")
    for _ in range(10):
        assert (await api.link(car, "mixa", "wrong-pass")).status_code == 401
    r = await api.link(car, "mixa")
    assert r.status_code == 429


async def test_provisioning_key_not_logged(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = await client.post(
            "/api/v1/cars/register", json={"name": "x"}, headers={"X-Provisioning-Key": PROVISIONING_KEY}
        )
    assert r.status_code == 201
    assert PROVISIONING_KEY not in caplog.text
    assert r.json()["car_token"] not in caplog.text
