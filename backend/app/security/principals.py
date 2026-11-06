"""Principal resolution (spec §7.1): User (Bearer JWT), Car (Car token), Driver-via-car, Provisioning."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.config import Settings
from app.db import get_session
from app.deps import get_clock, get_settings_dep
from app.errors import BadRequest, CarRevoked, DriverNotLinked, Forbidden, Unauthenticated
from app.models import Car, CarDriver, User
from app.security.jwt import decode_access_token
from app.security.tokens import hash_token

# These header schemes exist mostly so Swagger UI gets an "Authorize" dialog.
authorization_header = APIKeyHeader(
    name="Authorization",
    scheme_name="Authorization",
    description="`Bearer <access JWT>` for users, or `Car <car_token>` for the car app.",
    auto_error=False,
)
driver_id_header = APIKeyHeader(
    name="X-Driver-Id",
    scheme_name="DriverId",
    description="With a Car token: the linked driver (user UUID) the car acts for.",
    auto_error=False,
)
provisioning_header = APIKeyHeader(
    name="X-Provisioning-Key",
    scheme_name="ProvisioningKey",
    description="Shared secret for car registration.",
    auto_error=False,
)


@dataclass(frozen=True)
class CarPrincipal:
    car: Car


@dataclass(frozen=True)
class Actor:
    """A user acting directly (Bearer JWT) or through a car (Car token + X-Driver-Id)."""

    user: User
    car: Car | None = None

    @property
    def via_car(self) -> bool:
        return self.car is not None


def _split_auth(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    scheme, _, credential = value.strip().partition(" ")
    credential = credential.strip()
    if not credential:
        return None
    return scheme.lower(), credential


def _mark(request: Request, principal_type: str) -> None:
    request.state.principal_type = principal_type


async def _car_from_token(session: AsyncSession, token: str) -> Car:
    car = (await session.execute(select(Car).where(Car.token_hash == hash_token(token)))).scalar_one_or_none()
    if car is None:
        raise Unauthenticated("Invalid car token")
    if car.revoked_at is not None:
        raise CarRevoked()
    return car


async def _user_from_jwt(session: AsyncSession, token: str, settings: Settings, clock: Clock) -> User:
    user_id = decode_access_token(token, secret=settings.jwt_secret, now=clock.now())
    user = await session.get(User, user_id)
    if user is None:
        raise Unauthenticated("User no longer exists")
    return user


def _parse_driver_id(value: str | None) -> uuid.UUID:
    if not value:
        raise BadRequest("X-Driver-Id header is required with a Car token")
    try:
        return uuid.UUID(value.strip())
    except ValueError as exc:
        raise BadRequest("X-Driver-Id must be a UUID") from exc


async def _driver_via_car(session: AsyncSession, car: Car, driver_id: uuid.UUID) -> User:
    link = await session.get(CarDriver, (car.id, driver_id))
    if link is None or link.revoked_at is not None:
        raise DriverNotLinked()
    user = await session.get(User, driver_id)
    if user is None:  # pragma: no cover - FK cascade makes this impossible
        raise DriverNotLinked()
    return user


async def get_user_principal(
    request: Request,
    authorization: str | None = Depends(authorization_header),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    clock: Clock = Depends(get_clock),
) -> User:
    parsed = _split_auth(authorization)
    if parsed is None:
        raise Unauthenticated()
    scheme, credential = parsed
    if scheme != "bearer":
        raise Forbidden("This endpoint requires a user access token")
    user = await _user_from_jwt(session, credential, settings, clock)
    _mark(request, "user")
    return user


async def get_car_principal(
    request: Request,
    authorization: str | None = Depends(authorization_header),
    session: AsyncSession = Depends(get_session),
) -> CarPrincipal:
    parsed = _split_auth(authorization)
    if parsed is None:
        raise Unauthenticated()
    scheme, credential = parsed
    if scheme != "car":
        raise Forbidden("This endpoint requires a car token")
    car = await _car_from_token(session, credential)
    _mark(request, "car")
    return CarPrincipal(car)


async def get_driver_principal(
    request: Request,
    authorization: str | None = Depends(authorization_header),
    driver_id: str | None = Depends(driver_id_header),
    session: AsyncSession = Depends(get_session),
) -> Actor:
    parsed = _split_auth(authorization)
    if parsed is None:
        raise Unauthenticated()
    scheme, credential = parsed
    if scheme != "car":
        raise Forbidden("This endpoint requires a car token and X-Driver-Id")
    car = await _car_from_token(session, credential)
    user = await _driver_via_car(session, car, _parse_driver_id(driver_id))
    _mark(request, "driver")
    return Actor(user=user, car=car)


async def get_actor(
    request: Request,
    authorization: str | None = Depends(authorization_header),
    driver_id: str | None = Depends(driver_id_header),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    clock: Clock = Depends(get_clock),
) -> Actor:
    """User **or** Driver-via-car."""
    parsed = _split_auth(authorization)
    if parsed is None:
        raise Unauthenticated()
    scheme, credential = parsed
    if scheme == "bearer":
        user = await _user_from_jwt(session, credential, settings, clock)
        _mark(request, "user")
        return Actor(user=user)
    if scheme == "car":
        car = await _car_from_token(session, credential)
        user = await _driver_via_car(session, car, _parse_driver_id(driver_id))
        _mark(request, "driver")
        return Actor(user=user, car=car)
    raise Unauthenticated("Unsupported authorization scheme")


async def require_provisioning_key(
    request: Request,
    key: str | None = Depends(provisioning_header),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    if not key or not hmac.compare_digest(key.encode(), settings.car_provisioning_key.encode()):
        raise Unauthenticated("Invalid provisioning key")
    _mark(request, "provisioning")
