"""Service factories for routers."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.config import Settings
from app.db import get_session
from app.deps import get_clock, get_passwords, get_settings_dep, get_throttle
from app.security.passwords import PasswordService
from app.security.throttle import LoginThrottle
from app.services.accounts import AccountService
from app.services.cars import CarService
from app.services.drives import DriveService
from app.services.stats import StatsService


def account_service(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    clock: Clock = Depends(get_clock),
    passwords: PasswordService = Depends(get_passwords),
    throttle: LoginThrottle = Depends(get_throttle),
) -> AccountService:
    return AccountService(session, settings, clock, passwords, throttle)


def car_service(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    clock: Clock = Depends(get_clock),
) -> CarService:
    return CarService(session, settings, clock)


def drive_service(
    session: AsyncSession = Depends(get_session), clock: Clock = Depends(get_clock)
) -> DriveService:
    return DriveService(session, clock)


def stats_service(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    clock: Clock = Depends(get_clock),
) -> StatsService:
    return StatsService(session, settings, clock)
