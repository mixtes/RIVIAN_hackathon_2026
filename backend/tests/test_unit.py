"""Unit tests without the database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.cache import LeaderboardCache
from app.domain.mistakes import is_valid_mistake_type
from app.domain.periods import Period, bucket_step, bucket_unit, min_minutes, truncate, window_for
from app.errors import BadRequest, TokenExpired, TooManyAttempts, Unauthenticated
from app.security.jwt import decode_access_token, encode_access_token
from app.security.passwords import PasswordService
from app.security.throttle import LoginThrottle
from app.security.tokens import generate_car_token, generate_token, hash_token
from app.services.drives import decode_cursor, encode_cursor
from tests.conftest import NOW, make_settings


async def test_password_hash_and_verify() -> None:
    svc = PasswordService(make_settings())
    h = await svc.hash("correct horse battery")
    assert h.startswith("$argon2id$")
    assert await svc.verify(h, "correct horse battery")
    assert not await svc.verify(h, "wrong")
    assert not await svc.verify(None, "anything")  # unknown user path
    assert not await svc.verify("not-a-hash", "x")


def test_password_needs_rehash_when_params_change() -> None:
    weak = PasswordService(make_settings())
    strong = PasswordService(make_settings(argon2_time_cost=2))
    assert strong.needs_rehash(weak.hash_sync("pw-123456"))


def test_tokens() -> None:
    t1, t2 = generate_token(), generate_token()
    assert t1 != t2 and len(t1) >= 43
    car = generate_car_token()
    assert car.startswith("car_")
    assert len(hash_token(car)) == 64
    assert hash_token(car) == hash_token(car)


def test_jwt_roundtrip_and_expiry() -> None:
    uid = uuid.uuid4()
    tok = encode_access_token(uid, secret="s" * 40, ttl_min=15, now=NOW)
    assert tok.expires_in == 900
    assert decode_access_token(tok.token, secret="s" * 40, now=NOW + timedelta(minutes=14)) == uid
    with pytest.raises(TokenExpired):
        decode_access_token(tok.token, secret="s" * 40, now=NOW + timedelta(minutes=15))
    with pytest.raises(Unauthenticated):
        decode_access_token(tok.token, secret="other" * 10, now=NOW)
    with pytest.raises(Unauthenticated):
        decode_access_token("garbage", secret="s" * 40, now=NOW)


@pytest.mark.parametrize(
    ("period", "span", "unit", "step"),
    [
        (Period.DAY, timedelta(hours=24), "hour", "1 hour"),
        (Period.WEEK, timedelta(days=7), "day", "1 day"),
        (Period.MONTH, timedelta(days=30), "day", "1 day"),
        (Period.YEAR, timedelta(days=365), "month", "1 month"),
    ],
)
def test_period_windows(period: Period, span: timedelta, unit: str, step: str) -> None:
    w = window_for(period, NOW)
    assert w.end == NOW and w.start == NOW - span
    assert bucket_unit(period) == unit and bucket_step(period) == step


def test_period_all_is_unbounded_and_thresholds() -> None:
    assert window_for(Period.ALL, NOW).start is None
    s = make_settings()
    assert [min_minutes(p, s) for p in Period] == [10, 45, 120, 480, 480]


def test_truncate() -> None:
    ts = datetime(2026, 11, 6, 13, 45, 12, 5, tzinfo=UTC)
    assert truncate(ts, "hour") == datetime(2026, 11, 6, 13, tzinfo=UTC)
    assert truncate(ts, "day") == datetime(2026, 11, 6, tzinfo=UTC)
    assert truncate(ts, "month") == datetime(2026, 11, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        truncate(ts, "week")


@pytest.mark.parametrize(
    ("value", "ok"),
    [("EYES_OFF_ROAD", True), ("YAWN", True), ("X1", True), ("yawn", False), ("1ABC", False), ("A", False)],
)
def test_mistake_type_regex(value: str, ok: bool) -> None:
    assert is_valid_mistake_type(value) is ok


def test_cursor_roundtrip_and_garbage() -> None:
    did = uuid.uuid4()
    assert decode_cursor(encode_cursor(NOW, did)) == (NOW, did)
    with pytest.raises(BadRequest):
        decode_cursor("not-a-cursor")


def test_throttle_locks_after_max_failures() -> None:
    th = LoginThrottle(max_failures=3, window=timedelta(minutes=15), lockout=timedelta(minutes=15))
    for i in range(3):
        th.check("Mixa", NOW)
        th.record_failure("mixa", NOW + timedelta(seconds=i))
    with pytest.raises(TooManyAttempts):
        th.check("MIXA", NOW + timedelta(minutes=1))
    th.check("mixa", NOW + timedelta(minutes=16))  # lockout over


def test_throttle_window_expires_old_failures() -> None:
    th = LoginThrottle(max_failures=2, window=timedelta(minutes=15), lockout=timedelta(minutes=15))
    th.record_failure("a", NOW)
    th.record_failure("a", NOW + timedelta(minutes=16))  # first one fell out of the window
    th.check("a", NOW + timedelta(minutes=16))


def test_leaderboard_cache_ttl_and_invalidation() -> None:
    c = LeaderboardCache(60)
    c.put(("week", "RS", 50, 0), "rs", NOW)
    c.put(("week", "GLOBAL", 50, 0), "global", NOW)
    c.put(("week", "DE", 50, 0), "de", NOW)
    assert c.get(("week", "RS", 50, 0), NOW + timedelta(seconds=59)) == "rs"
    assert c.get(("week", "RS", 50, 0), NOW + timedelta(seconds=60)) is None
    c.invalidate_region("DE")
    assert c.get(("week", "DE", 50, 0), NOW) is None
    assert c.get(("week", "GLOBAL", 50, 0), NOW) is None
    assert LeaderboardCache(0).get(("week", "RS", 50, 0), NOW) is None
