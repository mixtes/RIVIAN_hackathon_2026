"""Demo seed data (spec §12.2).

    uv run python -m scripts.seed            # uses DATABASE_URL from the environment / .env

Creates ~40 users in RS, US, DE and NO, 3 cars and ~2,000 drives over the last 400 days, so every
leaderboard period has content. Includes the demo user `demo` / `demo-password-123` (RS), linked to
all three cars. Idempotent: deletes the previous seed rows (fixed usernames and car ids) and
re-creates them with a fixed random seed. The car tokens are new on every run and printed at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock, FrozenClock, SystemClock
from app.config import get_settings
from app.db import make_engine, make_sessionmaker
from app.domain.mistakes import KNOWN_MISTAKE_TYPES
from app.models import Car, CarDriver, Drive, DriveEvent, DriveMistake, User
from app.security.passwords import PasswordService
from app.security.tokens import generate_car_token, hash_token

SEED = 20261106
NAMESPACE = uuid.UUID("5b0b7c1e-6a51-4c7a-9d63-3f0f4a2f6c11")
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-password-123"
SEED_PASSWORD = "seed-password-123"
REGIONS = ("RS", "US", "DE", "NO")
DAYS = 400
COLORS = ("#4F7DF3", "#E8603C", "#2BB673", "#F5A524", "#9B59B6", "#16A085", "#E84393", "#7F8C8D")

FIRST_NAMES = (
    "Ana", "Marko", "Jelena", "Nikola", "Milica", "Stefan", "Ivana", "Luka", "Emma", "Liam", "Olivia",
    "Noah", "Ava", "Ethan", "Mia", "Lucas", "Lena", "Felix", "Hannah", "Paul", "Lea", "Jonas", "Sophie",
    "Leon", "Ingrid", "Lars", "Sofie", "Magnus", "Nora", "Henrik", "Astrid", "Erik", "Maja", "Jakob",
    "Sara", "Vuk", "Tara", "Ole", "Mila",
)  # fmt: skip

CARS = (
    ("Hackathon R1S", "R1S", "7PDSGABA1NN000001"),
    ("Family R1T", "R1T", "7PDSGABA1NN000002"),
    ("Fleet R2", "R2", "7PDSGABA1NN000003"),
)

# Mistake mix: (type, relative weight, typical occurrence duration in seconds or None)
MISTAKE_PROFILE = {
    "EYES_OFF_ROAD": (5.0, 2.5),
    "PHONE_USE": (2.0, 5.0),
    "EYES_CLOSED": (0.6, 1.5),
    "YAWN": (2.5, None),
    "HANDS_OFF_WHEEL": (1.2, 4.0),
    "PHONE_CALL": (1.0, 30.0),
    "EATING_DRINKING": (0.8, 20.0),
    "SMOKING": (0.3, 60.0),
}
assert set(MISTAKE_PROFILE) == {m.type for m in KNOWN_MISTAKE_TYPES}


def car_id(index: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"car-{index}")


def seed_usernames() -> list[str]:
    return [DEMO_USERNAME] + [
        f"{name.lower()}_{REGIONS[i % len(REGIONS)].lower()}" for i, name in enumerate(FIRST_NAMES)
    ]


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def make_drive(
    rng: random.Random, *, user_id: uuid.UUID, car: uuid.UUID, skill: float, ended_at: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    minutes = max(4.0, min(180.0, rng.lognormvariate(3.2, 0.6)))  # median ~25 min
    started_at = ended_at - timedelta(minutes=minutes)
    duration_s = int(minutes * 60)

    # Fewer mistakes for skilled drivers; more on long drives.
    rate_per_hour = max(0.2, (100 - skill) / 4.5)
    expected = rate_per_hour * minutes / 60
    total = max(0, round(rng.gauss(expected, expected**0.5 + 0.3)))
    mistakes: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    types = list(MISTAKE_PROFILE)
    weights = [MISTAKE_PROFILE[t][0] for t in types]
    for _ in range(total):
        mtype = rng.choices(types, weights)[0]
        typical = MISTAKE_PROFILE[mtype][1]
        dur = None if typical is None else round(max(0.5, rng.expovariate(1 / typical)), 1)
        if dur is not None:
            dur = min(dur, duration_s / 4)
        m = mistakes.setdefault(
            mtype, {"type": mtype, "count": 0, "total_duration_s": None, "max_duration_s": None}
        )
        m["count"] += 1
        if dur is not None:
            m["total_duration_s"] = round((m["total_duration_s"] or 0) + dur, 2)
            m["max_duration_s"] = max(m["max_duration_s"] or 0, dur)
        offset = rng.uniform(0, max(1.0, duration_s - (dur or 0)))
        events.append(
            {"type": mtype, "started_at": started_at + timedelta(seconds=offset), "duration_s": dur}
        )

    penalty = sum(m["count"] for m in mistakes.values()) * 2.2
    score = round(max(0.0, min(100.0, rng.gauss(skill, 4) - penalty * 0.6 + 4)), 2)

    drive_id = uuid.UUID(int=rng.getrandbits(128), version=4)
    payload = {
        "id": str(drive_id),
        "started_at": _iso(started_at),
        "ended_at": _iso(ended_at),
        "distance_km": None,
        "score": score,
        "engine_version": "seed/1.0",
        "mistakes": list(mistakes.values()),
        "events": [{**e, "started_at": _iso(e["started_at"])} for e in events],
        "extra": {"seed": True},
    }
    drive = {
        "id": drive_id,
        "user_id": user_id,
        "car_id": car,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": duration_s,
        "distance_km": None,
        "score": score,
        "engine_version": "seed/1.0",
        "raw_payload": payload,
        "received_at": ended_at + timedelta(minutes=rng.randint(1, 120)),
    }
    mistake_rows = [{"drive_id": drive_id, **m} for m in mistakes.values()]
    event_rows = [{"drive_id": drive_id, **e} for e in events]
    return drive, mistake_rows, event_rows


async def wipe(session: AsyncSession) -> None:
    cars = [car_id(i) for i in range(len(CARS))]
    await session.execute(delete(Drive).where(Drive.car_id.in_(cars)))
    await session.execute(delete(User).where(User.username.in_(seed_usernames())))
    await session.execute(delete(Car).where(Car.id.in_(cars)))


async def seed(session: AsyncSession, clock: Clock, *, drives_per_user: int = 50) -> dict[str, Any]:
    rng = random.Random(SEED)
    settings = get_settings()
    passwords = PasswordService(settings)
    now = clock.now()

    await wipe(session)

    car_tokens: list[tuple[str, uuid.UUID, str]] = []
    for i, (name, model, vin) in enumerate(CARS):
        token = generate_car_token()
        session.add(
            Car(
                id=car_id(i),
                name=name,
                model=model,
                vin=vin,
                token_hash=hash_token(token),
                created_at=now - timedelta(days=DAYS + 5),
                last_seen_at=now,
            )
        )
        car_tokens.append((name, car_id(i), token))
    await session.flush()

    seed_hash = passwords.hash_sync(SEED_PASSWORD)
    demo_hash = passwords.hash_sync(DEMO_PASSWORD)
    users: list[tuple[User, float, list[uuid.UUID]]] = []
    for i, username in enumerate(seed_usernames()):
        is_demo = username == DEMO_USERNAME
        region = "RS" if is_demo else REGIONS[(i - 1) % len(REGIONS)]
        display = "Demo Driver" if is_demo else FIRST_NAMES[i - 1]
        user = User(
            id=uuid.uuid5(NAMESPACE, f"user-{username}"),
            username=username,
            password_hash=demo_hash if is_demo else seed_hash,
            display_name=display,
            region_code=region,
            avatar_color=COLORS[i % len(COLORS)],
            leaderboard_visible=is_demo or i % 13 != 0,  # a few users opt out
            created_at=now - timedelta(days=DAYS + 2),
            updated_at=now - timedelta(days=DAYS + 2),
            last_login_at=now - timedelta(days=rng.randint(0, 30)),
        )
        session.add(user)
        skill = 84.0 if is_demo else max(55.0, min(98.0, rng.gauss(82, 8)))
        cars = [car_id(c) for c in range(len(CARS))] if is_demo else [car_id(i % len(CARS))]
        users.append((user, skill, cars))
    await session.flush()

    # Links: at most MAX_DRIVERS_PER_CAR active per car; the rest are historic (revoked).
    active: dict[uuid.UUID, int] = {}
    links = []
    for user, _, cars in users:
        for cid in cars:
            is_active = active.get(cid, 0) < settings.max_drivers_per_car
            active[cid] = active.get(cid, 0) + (1 if is_active else 0)
            links.append(
                {
                    "car_id": cid,
                    "user_id": user.id,
                    "linked_at": now - timedelta(days=DAYS + 1),
                    "last_used_at": now - timedelta(hours=rng.randint(1, 500)),
                    "revoked_at": None if is_active else now - timedelta(days=rng.randint(1, 60)),
                }
            )
    await session.execute(insert(CarDriver), links)

    drives, mistakes, events = [], [], []
    for user, skill, cars in users:
        count = drives_per_user + rng.randint(-15, 15)
        if user.username == DEMO_USERNAME:
            count = drives_per_user + 20
        for _ in range(count):
            # Bias towards recent days so short periods are populated too.
            days_ago = min(DAYS - 1, rng.expovariate(1 / 90))
            ended_at = now - timedelta(days=days_ago, minutes=rng.randint(5, 600))
            d, m, e = make_drive(rng, user_id=user.id, car=rng.choice(cars), skill=skill, ended_at=ended_at)
            drives.append(d)
            mistakes.extend(m)
            events.extend(e)
        # Guarantee recent activity for the demo user (day/week boards).
        if user.username == DEMO_USERNAME:
            for hours in (2, 20, 50, 100):
                d, m, e = make_drive(
                    rng, user_id=user.id, car=cars[0], skill=skill, ended_at=now - timedelta(hours=hours)
                )
                drives.append(d)
                mistakes.extend(m)
                events.extend(e)

    for i in range(0, len(drives), 1000):
        await session.execute(insert(Drive), drives[i : i + 1000])
    for i in range(0, len(mistakes), 2000):
        await session.execute(insert(DriveMistake), mistakes[i : i + 2000])
    for i in range(0, len(events), 2000):
        await session.execute(insert(DriveEvent), events[i : i + 2000])
    await session.commit()

    n_users = (await session.execute(select(User.id).where(User.username.in_(seed_usernames())))).all()
    return {
        "users": len(n_users),
        "drives": len(drives),
        "mistake_rows": len(mistakes),
        "events": len(events),
        "cars": car_tokens,
    }


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--drives-per-user", type=int, default=50)
    parser.add_argument("--now", help="Pretend 'now' (ISO-8601, UTC), for reproducible data")
    args = parser.parse_args(argv)
    clock: Clock = FrozenClock(datetime.fromisoformat(args.now)) if args.now else SystemClock()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            result = await seed(session, clock, drives_per_user=args.drives_per_user)
    finally:
        await engine.dispose()

    print(
        f"Seeded {result['users']} users, {result['drives']} drives, "
        f"{result['mistake_rows']} mistake rows, {result['events']} events."
    )
    print(f"Demo login: {DEMO_USERNAME} / {DEMO_PASSWORD}   (other seed users: <username> / {SEED_PASSWORD})")
    print("Car tokens (new on every run):")
    for name, cid, token in result["cars"]:
        print(f"  {name:<14} car_id={cid}  token={token}")


if __name__ == "__main__":
    asyncio.run(main())
