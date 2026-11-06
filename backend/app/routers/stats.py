from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.cache import LeaderboardCache
from app.deps import get_leaderboard_cache
from app.domain.periods import Period
from app.domain.regions import GLOBAL, is_valid_region, normalize_region
from app.errors import ValidationFailed
from app.routers._deps import stats_service
from app.schemas.errors import ERRORS
from app.schemas.stats import LeaderboardResponse, StatsResponse
from app.security.principals import Actor, get_actor
from app.services.stats import StatsService

stats_router = APIRouter(prefix="/api/v1/stats", tags=["stats"])
leaderboard_router = APIRouter(prefix="/api/v1/leaderboards", tags=["leaderboards"])


@stats_router.get(
    "/me", response_model=StatsResponse, responses=ERRORS(401, 403, 422), summary="Personal stats"
)
async def my_stats(
    period: Period = Query(Period.WEEK),
    actor: Actor = Depends(get_actor),
    svc: StatsService = Depends(stats_service),
) -> StatsResponse:
    return await svc.stats(actor.user, period)


@leaderboard_router.get(
    "",
    response_model=LeaderboardResponse,
    responses=ERRORS(401, 403, 422),
    summary="Leaderboard for a period and region (default: my home region), plus my position",
)
async def leaderboard(
    period: Period = Query(Period.WEEK),
    region: str | None = Query(
        None, description="ISO 3166-1 alpha-2 code, or GLOBAL. Default: my home region."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
    actor: Actor = Depends(get_actor),
    svc: StatsService = Depends(stats_service),
    cache: LeaderboardCache = Depends(get_leaderboard_cache),
) -> LeaderboardResponse:
    code = normalize_region(region) if region else actor.user.region_code
    if code != GLOBAL and not is_valid_region(code):
        raise ValidationFailed(
            details=[{"field": "region", "issue": "must be an ISO 3166-1 alpha-2 code or GLOBAL"}]
        )
    return await svc.leaderboard(
        actor.user, period=period, region=code, limit=limit, offset=offset, cache=cache
    )
