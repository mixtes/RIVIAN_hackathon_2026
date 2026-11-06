"""Shared FastAPI dependencies that read process-wide services from app.state."""

from __future__ import annotations

from fastapi import Request

from app.cache import LeaderboardCache
from app.clock import Clock
from app.config import Settings
from app.security.passwords import PasswordService
from app.security.throttle import LoginThrottle


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_passwords(request: Request) -> PasswordService:
    passwords: PasswordService = request.app.state.passwords
    return passwords


def get_throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.throttle
    return throttle


def get_leaderboard_cache(request: Request) -> LeaderboardCache:
    cache: LeaderboardCache = request.app.state.leaderboard_cache
    return cache
