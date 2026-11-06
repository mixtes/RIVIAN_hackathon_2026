from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from tests.conftest import NOW, Api, CarCtx, UserCtx, drive_body


async def add_drive(
    api: Api, car: CarCtx, user: UserCtx, *, ago: timedelta, minutes: float, score: float, **kw: Any
) -> None:
    r = await api.upload(car, user.id, drive_body(ended_at=NOW - ago, minutes=minutes, score=score, **kw))
    assert r.status_code == 201, r.text


# ---- personal stats -------------------------------------------------------------


@pytest.fixture
async def stats_user(api: Api) -> tuple[CarCtx, UserCtx]:
    car = await api.register_car()
    user = await api.driver(car, "mixa")
    await add_drive(api, car, user, ago=timedelta(hours=1), minutes=60, score=90)
    await add_drive(api, car, user, ago=timedelta(days=2), minutes=30, score=60)
    return car, user


async def test_stats_week(client: httpx.AsyncClient, stats_user: Any) -> None:
    car, user = stats_user
    r = await client.get("/api/v1/stats/me?period=week", headers=car.as_driver(user.id))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["period"] == "week"
    assert s["window_end"] == "2026-11-06T12:00:00Z"
    assert s["window_start"] == "2026-10-30T12:00:00Z"
    assert s["drive_count"] == 2
    assert s["total_duration_s"] == 5400
    assert s["avg_score"] == 75.0
    assert s["weighted_score"] == 80.0  # (90*3600 + 60*1800) / 5400
    assert (s["best_score"], s["worst_score"]) == (90.0, 60.0)
    assert s["mistakes"] == [
        {"type": "EYES_OFF_ROAD", "count": 8, "total_duration_s": 19.2, "per_hour": 5.33},
        {"type": "YAWN", "count": 4, "total_duration_s": None, "per_hour": 2.67},
    ]
    series = s["series"]
    assert len(series) == 8  # 2026-10-30 .. 2026-11-06, empty days included
    assert series[0]["bucket_start"] == "2026-10-30T00:00:00Z"
    by_day = {b["bucket_start"][:10]: b for b in series}
    assert by_day["2026-11-06"] == {
        "bucket_start": "2026-11-06T00:00:00Z",
        "drive_count": 1,
        "duration_s": 3600,
        "weighted_score": 90.0,
        "mistake_count": 6,
    }
    assert by_day["2026-11-04"]["weighted_score"] == 60.0
    assert by_day["2026-11-05"] == {
        "bucket_start": "2026-11-05T00:00:00Z",
        "drive_count": 0,
        "duration_s": 0,
        "weighted_score": None,
        "mistake_count": 0,
    }
    assert s["rank"] == {
        "region": {"code": "RS", "rank": 1, "total_ranked": 1, "qualified": True},
        "global": {"rank": 1, "total_ranked": 1, "qualified": True},
    }


@pytest.mark.parametrize(
    ("period", "buckets", "first_bucket", "drive_count"),
    [
        ("day", 25, "2026-11-05T12:00:00Z", 1),
        ("month", 31, "2026-10-07T00:00:00Z", 2),
        ("year", 13, "2025-11-01T00:00:00Z", 2),
        ("all", 1, "2026-11-01T00:00:00Z", 2),
    ],
)
async def test_stats_periods(
    client: httpx.AsyncClient, stats_user: Any, period: str, buckets: int, first_bucket: str, drive_count: int
) -> None:
    _, user = stats_user
    s = (await client.get(f"/api/v1/stats/me?period={period}", headers=user.headers)).json()
    assert len(s["series"]) == buckets
    assert s["series"][0]["bucket_start"] == first_bucket
    assert s["drive_count"] == drive_count
    assert sum(b["drive_count"] for b in s["series"]) == drive_count
    if period == "all":
        assert s["window_start"] == "2026-11-01T00:00:00Z"


async def test_stats_empty_user(api: Api, client: httpx.AsyncClient) -> None:
    user = await api.register_user("nobody")
    s = (await client.get("/api/v1/stats/me?period=all", headers=user.headers)).json()
    assert s["drive_count"] == 0 and s["total_duration_s"] == 0
    assert s["avg_score"] is None and s["weighted_score"] is None
    assert s["mistakes"] == []
    assert len(s["series"]) == 1
    assert s["rank"]["region"] == {"code": "RS", "rank": None, "total_ranked": 0, "qualified": False}
    s = (await client.get("/api/v1/stats/me?period=week", headers=user.headers)).json()
    assert len(s["series"]) == 8 and all(b["drive_count"] == 0 for b in s["series"])


async def test_stats_bad_period(api: Api, client: httpx.AsyncClient) -> None:
    user = await api.register_user("nobody")
    r = await client.get("/api/v1/stats/me?period=decade", headers=user.headers)
    assert r.status_code == 422


# ---- leaderboard -------------------------------------------------------------------


@pytest.fixture
async def board(api: Api, client: httpx.AsyncClient) -> dict[str, Any]:
    car = await api.register_car()
    users: dict[str, UserCtx] = {}
    spec = {
        "ana": ("RS", 60, 90),
        "bob": ("RS", 60, 95),
        "cleo": ("DE", 60, 99),
        "dan": ("RS", 30, 100),  # not qualified for week (45 min)
        "eve": ("RS", 60, 97),  # hidden
        "fay": ("RS", 60, 90),  # ties with ana
    }
    for name, (region, minutes, score) in spec.items():
        users[name] = await api.driver(car, name, region=region)
        await add_drive(api, car, users[name], ago=timedelta(hours=3), minutes=minutes, score=score)
    r = await client.patch(
        "/api/v1/users/me", json={"leaderboard_visible": False}, headers=users["eve"].headers
    )
    assert r.status_code == 200
    return {"car": car, "users": users}


async def test_leaderboard_region(client: httpx.AsyncClient, board: dict[str, Any]) -> None:
    ana = board["users"]["ana"]
    r = await client.get("/api/v1/leaderboards?period=week", headers=ana.headers)  # default: my region
    assert r.status_code == 200, r.text
    lb = r.json()
    assert lb["region"] == "RS" and lb["min_minutes"] == 45 and lb["total_ranked"] == 3
    assert (lb["entries"][0]["username"], lb["entries"][0]["rank"]) == ("bob", 1)
    tied = {(e["username"], e["rank"]) for e in lb["entries"][1:]}
    assert tied == {("ana", 2), ("fay", 2)}
    assert set(lb["entries"][0]) == {
        "rank",
        "user_id",
        "username",
        "display_name",
        "avatar_color",
        "weighted_score",
        "drive_count",
        "driving_time_s",
    }
    assert lb["me"] == {
        "rank": 2,
        "weighted_score": 90.0,
        "drive_count": 1,
        "driving_time_s": 3600,
        "qualified": True,
        "minutes_needed": 0,
    }


async def test_leaderboard_global_and_pagination(client: httpx.AsyncClient, board: dict[str, Any]) -> None:
    ana = board["users"]["ana"]
    lb = (await client.get("/api/v1/leaderboards?period=week&region=GLOBAL", headers=ana.headers)).json()
    assert lb["total_ranked"] == 4
    assert [e["username"] for e in lb["entries"]][:2] == ["cleo", "bob"]
    page = (
        await client.get(
            "/api/v1/leaderboards?period=week&region=global&limit=1&offset=3", headers=ana.headers
        )
    ).json()
    assert len(page["entries"]) == 1 and page["entries"][0]["rank"] == 3 and page["total_ranked"] == 4
    empty = (
        await client.get("/api/v1/leaderboards?period=week&region=GLOBAL&offset=50", headers=ana.headers)
    ).json()
    assert empty["entries"] == [] and empty["total_ranked"] == 4


async def test_leaderboard_unqualified_and_hidden(client: httpx.AsyncClient, board: dict[str, Any]) -> None:
    dan, eve = board["users"]["dan"], board["users"]["eve"]
    me = (await client.get("/api/v1/leaderboards?period=week", headers=dan.headers)).json()["me"]
    assert me == {
        "rank": None,
        "weighted_score": 100.0,
        "drive_count": 1,
        "driving_time_s": 1800,
        "qualified": False,
        "minutes_needed": 15,
    }
    me = (await client.get("/api/v1/leaderboards?period=week", headers=eve.headers)).json()["me"]
    assert me["qualified"] is True and me["rank"] is None
    # dan qualifies for the day board (10 min)
    day = (await client.get("/api/v1/leaderboards?period=day", headers=dan.headers)).json()
    assert day["entries"][0]["username"] == "dan" and day["me"]["rank"] == 1


async def test_leaderboard_other_region_and_validation(
    client: httpx.AsyncClient, board: dict[str, Any]
) -> None:
    ana = board["users"]["ana"]
    de = (await client.get("/api/v1/leaderboards?period=week&region=de", headers=ana.headers)).json()
    assert [e["username"] for e in de["entries"]] == ["cleo"]
    assert de["me"]["rank"] is None  # ana isn't in DE
    bad = await client.get("/api/v1/leaderboards?period=week&region=XX", headers=ana.headers)
    assert bad.status_code == 422


async def test_leaderboard_cache_invalidated_by_upload(
    api: Api, client: httpx.AsyncClient, board: dict[str, Any]
) -> None:
    car, dan, ana = board["car"], board["users"]["dan"], board["users"]["ana"]
    before = (await client.get("/api/v1/leaderboards?period=week", headers=ana.headers)).json()
    assert "dan" not in [e["username"] for e in before["entries"]]
    await add_drive(api, car, dan, ago=timedelta(hours=1), minutes=30, score=100)
    after = (await client.get("/api/v1/leaderboards?period=week", headers=ana.headers)).json()
    assert after["entries"][0]["username"] == "dan"
    assert after["total_ranked"] == 4


async def test_leaderboard_window_excludes_old_drives(api: Api, client: httpx.AsyncClient) -> None:
    car = await api.register_car()
    old = await api.driver(car, "oldtimer")
    await add_drive(api, car, old, ago=timedelta(days=8), minutes=60, score=99)
    week = (await client.get("/api/v1/leaderboards?period=week", headers=old.headers)).json()
    assert week["total_ranked"] == 0 and week["me"]["drive_count"] == 0 and week["me"]["minutes_needed"] == 45
    month = (await client.get("/api/v1/leaderboards?period=month", headers=old.headers)).json()
    assert month["total_ranked"] == 0  # 60 min < 120 min
    alltime = (await client.get("/api/v1/leaderboards?period=all", headers=old.headers)).json()
    assert alltime["window_start"] is None and alltime["me"]["minutes_needed"] == 420
