"""Aggregation SQL for personal stats and leaderboards (spec §9.2, §9.3)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---- leaderboard -------------------------------------------------------------

_LB_CTE = """
WITH agg AS (
    SELECT d.user_id,
           (SUM(d.score * d.duration_s) / NULLIF(SUM(d.duration_s), 0))::float8 AS weighted_score,
           SUM(d.duration_s)::bigint AS driving_time_s,
           COUNT(*)::bigint          AS drive_count
    FROM drives d
    JOIN users u ON u.id = d.user_id
    WHERE (CAST(:window_start AS timestamptz) IS NULL OR d.ended_at >= CAST(:window_start AS timestamptz))
      AND d.ended_at < CAST(:window_end AS timestamptz)
      AND u.leaderboard_visible
      AND (CAST(:region AS text) = 'GLOBAL' OR u.region_code = CAST(:region AS text))
    GROUP BY d.user_id
    HAVING SUM(d.duration_s) >= CAST(:min_seconds AS bigint) AND SUM(d.duration_s) > 0
),
ranked AS (
    SELECT agg.*,
           RANK() OVER (ORDER BY weighted_score DESC, driving_time_s DESC) AS rank,
           COUNT(*) OVER () AS total_ranked
    FROM agg
)
"""

_LB_PAGE_SQL = (
    _LB_CTE
    + """
SELECT r.rank, r.total_ranked, r.user_id, r.weighted_score, r.driving_time_s, r.drive_count,
       u.username::text AS username, u.display_name, u.avatar_color
FROM ranked r JOIN users u ON u.id = r.user_id
ORDER BY r.rank, r.user_id
LIMIT :limit OFFSET :offset
"""
)

_LB_COUNT_SQL = _LB_CTE + "SELECT COUNT(*) FROM ranked"

_LB_ME_SQL = _LB_CTE + "SELECT rank, total_ranked FROM ranked WHERE user_id = :me"

_PERSONAL_AGG_SQL = """
SELECT COUNT(*)::bigint AS drive_count,
       COALESCE(SUM(duration_s), 0)::bigint AS driving_time_s,
       (SUM(score * duration_s) / NULLIF(SUM(duration_s), 0))::float8 AS weighted_score
FROM drives
WHERE user_id = :me
  AND (CAST(:window_start AS timestamptz) IS NULL OR ended_at >= CAST(:window_start AS timestamptz))
  AND ended_at < CAST(:window_end AS timestamptz)
"""


@dataclass(frozen=True)
class BoardRow:
    rank: int
    user_id: uuid.UUID
    username: str
    display_name: str
    avatar_color: str
    weighted_score: float
    drive_count: int
    driving_time_s: int


@dataclass(frozen=True)
class PersonalAgg:
    drive_count: int
    driving_time_s: int
    weighted_score: float | None


def _lb_params(
    window_start: datetime | None, window_end: datetime, region: str, min_seconds: int
) -> dict[str, Any]:
    return {
        "window_start": window_start,
        "window_end": window_end,
        "region": region,
        "min_seconds": min_seconds,
    }


async def leaderboard_page(
    session: AsyncSession,
    *,
    window_start: datetime | None,
    window_end: datetime,
    region: str,
    min_seconds: int,
    limit: int,
    offset: int,
) -> tuple[list[BoardRow], int]:
    params = _lb_params(window_start, window_end, region, min_seconds) | {"limit": limit, "offset": offset}
    rows = (await session.execute(text(_LB_PAGE_SQL), params)).mappings().all()
    if rows:
        total = int(rows[0]["total_ranked"])
    else:
        total = int((await session.execute(text(_LB_COUNT_SQL), params)).scalar_one())
    return [
        BoardRow(
            rank=int(r["rank"]),
            user_id=r["user_id"],
            username=r["username"],
            display_name=r["display_name"],
            avatar_color=r["avatar_color"],
            weighted_score=float(r["weighted_score"]),
            drive_count=int(r["drive_count"]),
            driving_time_s=int(r["driving_time_s"]),
        )
        for r in rows
    ], total


async def rank_of(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    window_start: datetime | None,
    window_end: datetime,
    region: str,
    min_seconds: int,
) -> tuple[int | None, int]:
    """(rank or None if not on the board, total_ranked)."""
    params = _lb_params(window_start, window_end, region, min_seconds) | {"me": user_id}
    row = (await session.execute(text(_LB_ME_SQL), params)).mappings().first()
    if row is not None:
        return int(row["rank"]), int(row["total_ranked"])
    total = int((await session.execute(text(_LB_COUNT_SQL), params)).scalar_one())
    return None, total


async def personal_agg(
    session: AsyncSession, *, user_id: uuid.UUID, window_start: datetime | None, window_end: datetime
) -> PersonalAgg:
    params = {"me": user_id, "window_start": window_start, "window_end": window_end}
    r = (await session.execute(text(_PERSONAL_AGG_SQL), params)).mappings().one()
    ws = r["weighted_score"]
    return PersonalAgg(int(r["drive_count"]), int(r["driving_time_s"]), None if ws is None else float(ws))


# ---- personal stats ----------------------------------------------------------

_TOTALS_SQL = """
SELECT COUNT(*)::bigint AS drive_count,
       COALESCE(SUM(duration_s), 0)::bigint AS total_duration_s,
       AVG(score)::float8 AS avg_score,
       (SUM(score * duration_s) / NULLIF(SUM(duration_s), 0))::float8 AS weighted_score,
       MAX(score)::float8 AS best_score,
       MIN(score)::float8 AS worst_score
FROM drives
WHERE user_id = :me AND ended_at >= :window_start AND ended_at < :window_end
"""

_MISTAKES_SQL = """
SELECT m.type,
       SUM(m.count)::bigint AS count,
       SUM(m.total_duration_s)::float8 AS total_duration_s
FROM drive_mistakes m
JOIN drives d ON d.id = m.drive_id
WHERE d.user_id = :me AND d.ended_at >= :window_start AND d.ended_at < :window_end
GROUP BY m.type
HAVING SUM(m.count) > 0
ORDER BY count DESC, m.type
"""

_SERIES_SQL = """
WITH buckets AS (
    SELECT generate_series(
               date_trunc(CAST(:unit AS text), CAST(:window_start AS timestamptz), 'UTC'),
               CAST(:window_end AS timestamptz),
               INTERVAL '{step}'
           ) AS bucket_start
),
w AS (
    SELECT d.id, d.score, d.duration_s,
           date_trunc(CAST(:unit AS text), d.ended_at, 'UTC') AS bucket_start
    FROM drives d
    WHERE d.user_id = :me AND d.ended_at >= :window_start AND d.ended_at < :window_end
),
mc AS (
    SELECT dm.drive_id, SUM(dm.count) AS c
    FROM drive_mistakes dm
    WHERE dm.drive_id IN (SELECT id FROM w)
    GROUP BY dm.drive_id
)
SELECT b.bucket_start,
       COUNT(w.id)::bigint AS drive_count,
       COALESCE(SUM(w.duration_s), 0)::bigint AS duration_s,
       (SUM(w.score * w.duration_s) / NULLIF(SUM(w.duration_s), 0))::float8 AS weighted_score,
       COALESCE(SUM(mc.c), 0)::bigint AS mistake_count
FROM buckets b
LEFT JOIN w  ON w.bucket_start = b.bucket_start
LEFT JOIN mc ON mc.drive_id = w.id
GROUP BY b.bucket_start
ORDER BY b.bucket_start
"""

_ALLOWED_STEPS = frozenset({"1 hour", "1 day", "1 month"})

_FIRST_DRIVE_SQL = "SELECT MIN(ended_at) FROM drives WHERE user_id = :me"


async def first_drive_end(session: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    value: datetime | None = (await session.execute(text(_FIRST_DRIVE_SQL), {"me": user_id})).scalar_one()
    return value


async def totals(
    session: AsyncSession, *, user_id: uuid.UUID, window_start: datetime, window_end: datetime
) -> dict[str, Any]:
    params = {"me": user_id, "window_start": window_start, "window_end": window_end}
    return dict((await session.execute(text(_TOTALS_SQL), params)).mappings().one())


async def mistakes(
    session: AsyncSession, *, user_id: uuid.UUID, window_start: datetime, window_end: datetime
) -> list[dict[str, Any]]:
    params = {"me": user_id, "window_start": window_start, "window_end": window_end}
    return [dict(r) for r in (await session.execute(text(_MISTAKES_SQL), params)).mappings().all()]


async def series(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
    unit: str,
    step: str,
) -> list[dict[str, Any]]:
    if step not in _ALLOWED_STEPS:  # the step is inlined into SQL, so only whitelisted literals
        raise ValueError(f"unsupported step {step!r}")
    params = {"me": user_id, "window_start": window_start, "window_end": window_end, "unit": unit}
    sql = _SERIES_SQL.format(step=step)
    return [dict(r) for r in (await session.execute(text(sql), params)).mappings().all()]
