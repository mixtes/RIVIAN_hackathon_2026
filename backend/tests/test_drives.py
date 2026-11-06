from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest

from tests.conftest import NOW, Api, CarCtx, UserCtx, drive_body


@pytest.fixture
async def setup(api: Api) -> tuple[CarCtx, UserCtx]:
    car = await api.register_car("Hackathon R1S")
    user = await api.driver(car, "mixa")
    return car, user


async def test_upload_created_then_idempotent(api: Api, client: httpx.AsyncClient, setup: Any) -> None:
    car, user = setup
    body = drive_body(minutes=33.5, score=87.456)
    r = await api.upload(car, user.id, body)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["id"] == body["id"]
    assert d["duration_s"] == 2010
    assert d["score"] == 87.46
    assert d["car"] == {"id": car.id, "name": "Hackathon R1S"}
    assert d["mistakes_summary"] == {"total_count": 6, "types": ["EYES_OFF_ROAD", "YAWN"]}
    assert d["mistakes"][0] == {
        "type": "EYES_OFF_ROAD",
        "count": 4,
        "total_duration_s": 9.6,
        "max_duration_s": 3.1,
    }
    assert d["mistakes"][1]["total_duration_s"] is None
    assert len(d["events"]) == 1 and d["events"][0]["duration_s"] == 3.1
    assert d["engine_version"] == "test-engine/1.0"
    assert d["distance_km"] is None

    again = await api.upload(car, user.id, body)
    assert again.status_code == 200 and again.json()["id"] == body["id"]

    info = (await client.get("/api/v1/cars/me", headers=car.headers)).json()
    assert info["drivers"][0]["last_used_at"] is not None


async def test_upload_accepts_engine_routing_fields(api: Api, setup: Any) -> None:
    car, user = setup
    body = drive_body()
    body["session_id"] = body["id"]
    body["user_id"] = user.id
    body["some_future_field"] = {"x": 1}
    assert (await api.upload(car, user.id, body)).status_code == 201


async def test_upload_conflicts(api: Api, setup: Any) -> None:
    car, user = setup
    body = drive_body()
    assert (await api.upload(car, user.id, body)).status_code == 201

    changed = {**body, "score": 50}
    r = await api.upload(car, user.id, changed)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DRIVE_CONFLICT"

    other = await api.driver(car, "other")
    r = await api.upload(car, other.id, body)
    assert r.status_code == 409


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(score=100.5),
        lambda b: b.update(score=-1),
        lambda b: b.update(ended_at="2026-11-06T09:00:00Z", started_at="2026-11-06T10:00:00Z"),
        lambda b: b.update(started_at="2026-11-04T09:00:00Z", ended_at="2026-11-05T10:00:00Z"),  # > 24 h
        lambda b: b.update(started_at="2023-12-31T23:00:00Z", ended_at="2023-12-31T23:30:00Z"),
        lambda b: b.update(ended_at="2026-11-06T12:11:00Z"),  # > now + 10 min
        lambda b: b.update(ended_at="2026-11-06T10:00:00"),  # no offset
        lambda b: b.update(distance_km=2001),
        lambda b: b.update(mistakes=[{"type": "YAWN", "count": 1}, {"type": "YAWN", "count": 2}]),
        lambda b: b.update(mistakes=[{"type": "yawn", "count": 1}]),
        lambda b: b.update(mistakes=[{"type": "YAWN", "count": -1}]),
        lambda b: b.update(mistakes=[{"type": "YAWN", "count": 1, "total_duration_s": 999999}]),
        lambda b: b.update(events=[{"type": "YAWN", "started_at": "2026-11-06T02:00:00Z"}]),
        lambda b: b.update(session_id=str(uuid.uuid4())),
        lambda b: b.update(user_id=str(uuid.uuid4())),
        lambda b: b.update(id="nope"),
        lambda b: b.pop("score"),
    ],
)
async def test_upload_validation(api: Api, setup: Any, mutate: Any) -> None:
    car, user = setup
    body = drive_body(ended_at=NOW - timedelta(hours=2))
    mutate(body)
    r = await api.upload(car, user.id, body)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_upload_auth_rules(api: Api, client: httpx.AsyncClient, setup: Any) -> None:
    car, user = setup
    body = drive_body()
    stranger = await api.register_user("stranger")
    r = await api.upload(car, stranger.id, body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "DRIVER_NOT_LINKED"
    r = await client.post("/api/v1/drives", json=body, headers=car.headers)  # no X-Driver-Id
    assert r.status_code == 400
    r = await client.post("/api/v1/drives", json=body, headers=user.headers)  # user JWT can't upload
    assert r.status_code == 403


async def test_unknown_mistake_types_are_stored(api: Api, client: httpx.AsyncClient, setup: Any) -> None:
    car, user = setup
    body = drive_body(
        mistakes=[{"type": "DROWSINESS", "count": 3, "total_duration_s": None, "max_duration_s": None}]
    )
    r = await api.upload(car, user.id, body)
    assert r.status_code == 201
    assert r.json()["mistakes"][0]["type"] == "DROWSINESS"


async def test_history_pagination_and_filter(api: Api, client: httpx.AsyncClient, setup: Any) -> None:
    car, user = setup
    car2 = await api.register_car("Second")
    assert (await api.link(car2, "mixa")).status_code == 201
    ids = []
    for i in range(5):
        body = drive_body(ended_at=NOW - timedelta(hours=5 - i))
        ids.append(body["id"])
        target = car2 if i == 4 else car
        assert (await api.upload(target, user.id, body)).status_code == 201

    page1 = (await client.get("/api/v1/drives?limit=2", headers=user.headers)).json()
    assert [d["id"] for d in page1["items"]] == [ids[4], ids[3]]
    assert page1["items"][0]["car"]["name"] == "Second"
    page2 = (
        await client.get(f"/api/v1/drives?limit=2&cursor={page1['next_cursor']}", headers=user.headers)
    ).json()
    assert [d["id"] for d in page2["items"]] == [ids[2], ids[1]]
    page3 = (
        await client.get(f"/api/v1/drives?limit=2&cursor={page2['next_cursor']}", headers=user.headers)
    ).json()
    assert [d["id"] for d in page3["items"]] == [ids[0]]
    assert page3["next_cursor"] is None

    only = (await client.get(f"/api/v1/drives?car_id={car2.id}", headers=car.as_driver(user.id))).json()
    assert [d["id"] for d in only["items"]] == [ids[4]]

    bad = await client.get("/api/v1/drives?cursor=garbage", headers=user.headers)
    assert bad.status_code == 400


async def test_detail_ownership(api: Api, client: httpx.AsyncClient, setup: Any) -> None:
    car, user = setup
    body = drive_body()
    await api.upload(car, user.id, body)
    r = await client.get(f"/api/v1/drives/{body['id']}", headers=car.as_driver(user.id))
    assert r.status_code == 200 and r.json()["received_at"]
    other = await api.driver(car, "other")
    r = await client.get(f"/api/v1/drives/{body['id']}", headers=other.headers)
    assert r.status_code == 404
    r = await client.get(f"/api/v1/drives/{uuid.uuid4()}", headers=user.headers)
    assert r.status_code == 404


async def test_unlinked_driver_keeps_uploaded_drives(api: Api, client: httpx.AsyncClient, setup: Any) -> None:
    car, user = setup
    body = drive_body()
    await api.upload(car, user.id, body)
    await client.delete(f"/api/v1/cars/me/drivers/{user.id}", headers=car.headers)
    r = await client.get("/api/v1/drives", headers=user.headers)
    assert [d["id"] for d in r.json()["items"]] == [body["id"]]
