"""Personal statistics and leaderboards (spec §8.8, §8.9, §9)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import LeaderboardCache
from app.clock import Clock
from app.config import Settings
from app.domain.periods import Period, bucket_step, bucket_unit, min_minutes, truncate, window_for
from app.domain.regions import GLOBAL
from app.models import User
from app.repositories import stats as stats_repo
from app.schemas.stats import (
    GlobalRank,
    LeaderboardEntry,
    LeaderboardMe,
    LeaderboardResponse,
    RankInfo,
    RegionRank,
    SeriesBucket,
    StatsMistake,
    StatsResponse,
)


def _r2(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


@dataclass(frozen=True)
class _CachedPage:
    entries: list[LeaderboardEntry]
    total_ranked: int


@dataclass
class StatsService:
    session: AsyncSession
    settings: Settings
    clock: Clock

    # ---- leaderboard -------------------------------------------------------------

    async def leaderboard(
        self, user: User, *, period: Period, region: str, limit: int, offset: int, cache: LeaderboardCache
    ) -> LeaderboardResponse:
        now = self.clock.now()
        window = window_for(period, now)
        min_min = min_minutes(period, self.settings)
        min_seconds = min_min * 60

        key = (period.value, region, limit, offset)
        page = cache.get(key, now)
        if not isinstance(page, _CachedPage):
            rows, total = await stats_repo.leaderboard_page(
                self.session,
                window_start=window.start,
                window_end=window.end,
                region=region,
                min_seconds=min_seconds,
                limit=limit,
                offset=offset,
            )
            page = _CachedPage(
                entries=[
                    LeaderboardEntry(
                        rank=r.rank,
                        user_id=r.user_id,
                        username=r.username,
                        display_name=r.display_name,
                        avatar_color=r.avatar_color,
                        weighted_score=round(r.weighted_score, 2),
                        drive_count=r.drive_count,
                        driving_time_s=r.driving_time_s,
                    )
                    for r in rows
                ],
                total_ranked=total,
            )
            cache.put(key, page, now)

        # The `me` row is never cached.
        agg = await stats_repo.personal_agg(
            self.session, user_id=user.id, window_start=window.start, window_end=window.end
        )
        rank, _ = await stats_repo.rank_of(
            self.session,
            user_id=user.id,
            window_start=window.start,
            window_end=window.end,
            region=region,
            min_seconds=min_seconds,
        )
        qualified = agg.driving_time_s >= min_seconds and agg.driving_time_s > 0
        minutes_needed = 0 if qualified else math.ceil(max(0, min_seconds - agg.driving_time_s) / 60) or 1
        me = LeaderboardMe(
            rank=rank if (qualified and user.leaderboard_visible) else None,
            weighted_score=_r2(agg.weighted_score),
            drive_count=agg.drive_count,
            driving_time_s=agg.driving_time_s,
            qualified=qualified,
            minutes_needed=minutes_needed,
        )
        return LeaderboardResponse(
            period=period,
            region=region,
            window_start=window.start,
            window_end=window.end,
            min_minutes=min_min,
            total_ranked=page.total_ranked,
            entries=page.entries,
            me=me,
            generated_at=now,
        )

    # ---- personal stats ----------------------------------------------------------

    async def stats(self, user: User, period: Period) -> StatsResponse:
        now = self.clock.now()
        window = window_for(period, now)
        unit, step = bucket_unit(period), bucket_step(period)
        if window.start is None:  # period=all: start at the first drive's month (spec §9.3)
            first = await stats_repo.first_drive_end(self.session, user.id)
            start = truncate(first, unit) if first is not None else now
        else:
            start = window.start
        end = window.end

        t = await stats_repo.totals(self.session, user_id=user.id, window_start=start, window_end=end)
        total_s = int(t["total_duration_s"])
        hours = total_s / 3600
        mistakes = [
            StatsMistake(
                type=m["type"],
                count=int(m["count"]),
                total_duration_s=_r2(m["total_duration_s"]),
                per_hour=round(int(m["count"]) / hours, 2) if hours > 0 else None,
            )
            for m in await stats_repo.mistakes(
                self.session, user_id=user.id, window_start=start, window_end=end
            )
        ]
        series = [
            SeriesBucket(
                bucket_start=b["bucket_start"],
                drive_count=int(b["drive_count"]),
                duration_s=int(b["duration_s"]),
                weighted_score=_r2(b["weighted_score"]),
                mistake_count=int(b["mistake_count"]),
            )
            for b in await stats_repo.series(
                self.session, user_id=user.id, window_start=start, window_end=end, unit=unit, step=step
            )
        ]

        min_seconds = min_minutes(period, self.settings) * 60
        qualified = total_s >= min_seconds and total_s > 0
        ranks = {}
        for region in (user.region_code, GLOBAL):
            rank, total_ranked = await stats_repo.rank_of(
                self.session,
                user_id=user.id,
                window_start=window.start,
                window_end=window.end,
                region=region,
                min_seconds=min_seconds,
            )
            ranks[region] = (rank if qualified and user.leaderboard_visible else None, total_ranked)

        return StatsResponse(
            period=period,
            window_start=start,
            window_end=end,
            drive_count=int(t["drive_count"]),
            total_duration_s=total_s,
            avg_score=_r2(t["avg_score"]),
            weighted_score=_r2(t["weighted_score"]),
            best_score=_r2(t["best_score"]),
            worst_score=_r2(t["worst_score"]),
            mistakes=mistakes,
            series=series,
            rank=RankInfo(
                region=RegionRank(
                    code=user.region_code,
                    rank=ranks[user.region_code][0],
                    total_ranked=ranks[user.region_code][1],
                    qualified=qualified,
                ),
                global_=GlobalRank(rank=ranks[GLOBAL][0], total_ranked=ranks[GLOBAL][1], qualified=qualified),
            ),
        )
