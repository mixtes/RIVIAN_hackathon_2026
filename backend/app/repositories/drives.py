from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Car, Drive, DriveEvent, DriveMistake


@dataclass(frozen=True)
class DriveRow:
    id: uuid.UUID
    car_id: uuid.UUID
    car_name: str
    started_at: datetime
    ended_at: datetime
    duration_s: int
    score: float
    total_mistakes: int
    mistake_types: list[str]


async def get(session: AsyncSession, drive_id: uuid.UUID) -> Drive | None:
    return await session.get(Drive, drive_id)


def add(session: AsyncSession, drive: Drive, mistakes: list[DriveMistake], events: list[DriveEvent]) -> None:
    session.add(drive)
    session.add_all(mistakes)
    session.add_all(events)


_LIST_SQL = """
SELECT d.id, d.car_id, c.name AS car_name, d.started_at, d.ended_at, d.duration_s,
       d.score::float8 AS score,
       COALESCE(m.total_count, 0) AS total_mistakes,
       COALESCE(m.types, ARRAY[]::varchar[]) AS mistake_types
FROM drives d
JOIN cars c ON c.id = d.car_id
LEFT JOIN LATERAL (
    SELECT SUM(dm.count)::bigint AS total_count,
           array_agg(dm.type ORDER BY dm.count DESC, dm.type) FILTER (WHERE dm.count > 0) AS types
    FROM drive_mistakes dm
    WHERE dm.drive_id = d.id
) m ON TRUE
WHERE d.user_id = :user_id
  {car_filter}
  {cursor_filter}
ORDER BY d.ended_at DESC, d.id DESC
LIMIT :limit
"""


async def list_page(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int,
    car_id: uuid.UUID | None,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[DriveRow]:
    params: dict[str, Any] = {"user_id": user_id, "limit": limit}
    car_filter = ""
    cursor_filter = ""
    if car_id is not None:
        car_filter = "AND d.car_id = :car_id"
        params["car_id"] = car_id
    if after is not None:
        cursor_filter = "AND (d.ended_at, d.id) < (:c_ended, :c_id)"
        params["c_ended"], params["c_id"] = after
    sql = _LIST_SQL.format(car_filter=car_filter, cursor_filter=cursor_filter)
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [
        DriveRow(
            id=r["id"],
            car_id=r["car_id"],
            car_name=r["car_name"],
            started_at=r["started_at"],
            ended_at=r["ended_at"],
            duration_s=r["duration_s"],
            score=float(r["score"]),
            total_mistakes=int(r["total_mistakes"]),
            mistake_types=list(r["mistake_types"] or []),
        )
        for r in rows
    ]


async def detail_parts(
    session: AsyncSession, drive_id: uuid.UUID
) -> tuple[Car, list[DriveMistake], list[DriveEvent]] | None:
    drive = await session.get(Drive, drive_id)
    if drive is None:
        return None
    car = await session.get(Car, drive.car_id)
    assert car is not None
    mistakes = (
        (await session.execute(select(DriveMistake).where(DriveMistake.drive_id == drive_id))).scalars().all()
    )
    events = (
        (
            await session.execute(
                select(DriveEvent).where(DriveEvent.drive_id == drive_id).order_by(DriveEvent.started_at)
            )
        )
        .scalars()
        .all()
    )
    ordered = sorted(mistakes, key=lambda m: (-m.count, m.type))
    return car, ordered, list(events)
