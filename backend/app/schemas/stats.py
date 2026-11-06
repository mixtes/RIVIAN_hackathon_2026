"""Personal stats + leaderboard DTOs (spec §8.8, §8.9)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.periods import Period


class StatsMistake(BaseModel):
    type: str
    count: int
    total_duration_s: float | None
    per_hour: float | None


class SeriesBucket(BaseModel):
    bucket_start: datetime
    drive_count: int
    duration_s: int
    weighted_score: float | None
    mistake_count: int


class RegionRank(BaseModel):
    code: str
    rank: int | None
    total_ranked: int
    qualified: bool


class GlobalRank(BaseModel):
    rank: int | None
    total_ranked: int
    qualified: bool


class RankInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    region: RegionRank
    global_: GlobalRank = Field(alias="global")


class StatsResponse(BaseModel):
    period: Period
    window_start: datetime | None
    window_end: datetime
    drive_count: int
    total_duration_s: int
    avg_score: float | None
    weighted_score: float | None
    best_score: float | None
    worst_score: float | None
    mistakes: list[StatsMistake]
    series: list[SeriesBucket]
    rank: RankInfo


class LeaderboardEntry(BaseModel):
    rank: int
    user_id: uuid.UUID
    username: str
    display_name: str
    avatar_color: str
    weighted_score: float
    drive_count: int
    driving_time_s: int


class LeaderboardMe(BaseModel):
    rank: int | None
    weighted_score: float | None
    drive_count: int
    driving_time_s: int
    qualified: bool
    minutes_needed: int


class LeaderboardResponse(BaseModel):
    period: Period
    region: str
    window_start: datetime | None
    window_end: datetime
    min_minutes: int
    total_ranked: int
    entries: list[LeaderboardEntry]
    me: LeaderboardMe
    generated_at: datetime
