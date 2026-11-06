from __future__ import annotations

import httpx
import pytest

from app.clock import FrozenClock
from tests.conftest import PASSWORD, Api


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


async def test_request_id_generated_and_echoed(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert len(r.headers["x-request-id"]) >= 16
    r = await client.get("/health", headers={"X-Request-Id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


async def test_register_returns_profile_and_tokens(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "Mixa",
            "password": PASSWORD,
            "display_name": " Mihajlo ",
            "region_code": "rs",
            "avatar_color": "#e8603c",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    user = body["user"]
    assert user["username"] == "mixa"
    assert user["display_name"] == "Mihajlo"
    assert user["region_code"] == "RS"
    assert user["avatar_color"] == "#E8603C"
    assert user["leaderboard_visible"] is True
    assert body["tokens"]["token_type"] == "bearer"
    assert body["tokens"]["expires_in"] == 900

    me = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {body['tokens']['access_token']}"}
    )
    assert me.status_code == 200 and me.json()["id"] == user["id"]


async def test_register_duplicate_username_is_case_insensitive(api: Api, client: httpx.AsyncClient) -> None:
    await api.register_user("mixa")
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": "MIXA", "password": PASSWORD, "display_name": "x", "region_code": "RS"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "USERNAME_TAKEN"


async def test_register_duplicate_email(api: Api, client: httpx.AsyncClient) -> None:
    await api.register_user("one", email="a@example.com")
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "two",
            "password": PASSWORD,
            "display_name": "x",
            "region_code": "RS",
            "email": "A@example.com",
        },
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"username": "ab"}, "username"),
        ({"username": "bad name"}, "username"),
        ({"username": "admin"}, "username"),
        ({"password": "short"}, "password"),
        ({"region_code": "XX"}, "region_code"),
        ({"region_code": "GLOBAL"}, "region_code"),
        ({"avatar_color": "red"}, "avatar_color"),
        ({"display_name": "   "}, "display_name"),
        ({"username": "samepass1", "password": "samepass1"}, "body"),
    ],
)
async def test_register_validation(client: httpx.AsyncClient, patch: dict[str, str], field: str) -> None:
    body = {"username": "valid_user", "password": PASSWORD, "display_name": "V", "region_code": "RS"}
    body.update(patch)
    r = await client.post("/api/v1/auth/register", json=body)
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["request_id"]
    assert any(d["field"] == field for d in err["details"]), err["details"]


async def test_login_success_and_uniform_failure(api: Api, client: httpx.AsyncClient) -> None:
    await api.register_user("mixa")
    ok = await client.post("/api/v1/auth/login", json={"username": "MIXA", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["user"]["username"] == "mixa"

    wrong_pw = await client.post("/api/v1/auth/login", json={"username": "mixa", "password": "nope-nope"})
    no_user = await client.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope-nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["error"]["code"] == no_user.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert wrong_pw.json()["error"]["message"] == no_user.json()["error"]["message"]


async def test_login_brute_force_lockout(api: Api, client: httpx.AsyncClient, clock: FrozenClock) -> None:
    await api.register_user("mixa")
    for _ in range(10):
        r = await client.post("/api/v1/auth/login", json={"username": "mixa", "password": "wrong-pass"})
        assert r.status_code == 401
    r = await client.post("/api/v1/auth/login", json={"username": "mixa", "password": PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"
    assert "retry-after" in r.headers
    clock.advance(minutes=16)
    r = await client.post("/api/v1/auth/login", json={"username": "mixa", "password": PASSWORD})
    assert r.status_code == 200


async def test_refresh_rotation_and_reuse_detection(api: Api, client: httpx.AsyncClient) -> None:
    user = await api.register_user("mixa")
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh})
    assert r1.status_code == 200
    new_refresh = r1.json()["refresh_token"]
    assert new_refresh != user.refresh

    # Presenting the rotated (revoked) token again revokes the whole family.
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh})
    assert reuse.status_code == 401 and reuse.json()["error"]["code"] == "TOKEN_REUSED"
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401 and after.json()["error"]["code"] == "TOKEN_REUSED"


async def test_refresh_expired_and_unknown(api: Api, client: httpx.AsyncClient, clock: FrozenClock) -> None:
    user = await api.register_user("mixa")
    unknown = await client.post("/api/v1/auth/refresh", json={"refresh_token": "nope"})
    assert unknown.status_code == 401 and unknown.json()["error"]["code"] == "UNAUTHENTICATED"
    clock.advance(days=31)
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh})
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_access_token_expiry(api: Api, client: httpx.AsyncClient, clock: FrozenClock) -> None:
    user = await api.register_user("mixa")
    clock.advance(minutes=16)
    r = await client.get("/api/v1/users/me", headers=user.headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_logout_revokes_family(api: Api, client: httpx.AsyncClient) -> None:
    user = await api.register_user("mixa")
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": user.refresh}, headers=user.headers)
    assert r.status_code == 204
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh})
    assert r.status_code == 401


async def test_unauthenticated_and_wrong_scheme(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/users/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    r = await client.get("/api/v1/users/me", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    r = await client.get("/api/v1/users/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_patch_profile(api: Api, client: httpx.AsyncClient) -> None:
    user = await api.register_user("mixa")
    r = await client.patch(
        "/api/v1/users/me",
        json={
            "display_name": "Mika",
            "region_code": "de",
            "avatar_color": "#2bb673",
            "leaderboard_visible": False,
        },
        headers=user.headers,
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["display_name"], p["region_code"], p["avatar_color"], p["leaderboard_visible"]) == (
        "Mika",
        "DE",
        "#2BB673",
        False,
    )
    bad = await client.patch("/api/v1/users/me", json={"display_name": None}, headers=user.headers)
    assert bad.status_code == 422
    unknown = await client.patch("/api/v1/users/me", json={"username": "x"}, headers=user.headers)
    assert unknown.status_code == 422


async def test_change_password(api: Api, client: httpx.AsyncClient) -> None:
    user = await api.register_user("mixa")
    wrong = await client.put(
        "/api/v1/users/me/password",
        json={"current_password": "nope", "new_password": "a brand new pass"},
        headers=user.headers,
    )
    assert wrong.status_code == 401
    ok = await client.put(
        "/api/v1/users/me/password",
        json={"current_password": PASSWORD, "new_password": "a brand new pass"},
        headers=user.headers,
    )
    assert ok.status_code == 204
    # Old refresh tokens are revoked; the new password works.
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh})
    ).status_code == 401
    login = await client.post("/api/v1/auth/login", json={"username": "mixa", "password": "a brand new pass"})
    assert login.status_code == 200


async def test_meta_endpoints(client: httpx.AsyncClient) -> None:
    types = (await client.get("/api/v1/meta/mistake-types")).json()
    assert [t["type"] for t in types][:2] == ["EYES_OFF_ROAD", "PHONE_USE"]
    assert {"type": "YAWN", "label": "Yawn", "has_duration": False} in types
    assert len(types) == 8
    regions = (await client.get("/api/v1/meta/regions")).json()
    assert {"code": "RS", "name": "Serbia"} in regions
    names = [r["name"] for r in regions]
    assert names == sorted(names) and len(regions) > 200


async def test_body_too_large(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login",
        content=b"{" + b" " * (1_048_577) + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_unknown_route_uses_error_format(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


async def test_chunked_body_too_large(client: httpx.AsyncClient) -> None:
    async def gen():  # type: ignore[no-untyped-def]
        for _ in range(5):
            yield b" " * 300_000

    r = await client.post("/api/v1/auth/login", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


async def test_chunked_small_body_is_replayed(client: httpx.AsyncClient) -> None:
    async def gen():  # type: ignore[no-untyped-def]
        yield b'{"username": "ghost", '
        yield b'"password": "nope-nope"}'

    r = await client.post("/api/v1/auth/login", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_unhandled_error_returns_500_envelope(app: object, client: httpx.AsyncClient) -> None:
    from fastapi import FastAPI

    assert isinstance(app, FastAPI)

    async def boom() -> None:
        raise RuntimeError("kaboom")

    app.add_api_route("/boom", boom)
    r = await client.get("/boom", headers={"X-Request-Id": "req-500"})
    assert r.status_code == 500
    assert r.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "Internal server error",
        "details": [],
        "request_id": "req-500",
    }
    assert r.headers["x-request-id"] == "req-500"
