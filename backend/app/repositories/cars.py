from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Car, CarDriver, User


async def lock_car(session: AsyncSession, car_id: uuid.UUID) -> Car:
    """Row-lock the car so concurrent link requests can't exceed the driver limit."""
    stmt = select(Car).where(Car.id == car_id).with_for_update()
    return (await session.execute(stmt)).scalar_one()


async def active_driver_count(session: AsyncSession, car_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(CarDriver)
        .where(CarDriver.car_id == car_id, CarDriver.revoked_at.is_(None))
    )
    return int((await session.execute(stmt)).scalar_one())


async def get_link(session: AsyncSession, car_id: uuid.UUID, user_id: uuid.UUID) -> CarDriver | None:
    return await session.get(CarDriver, (car_id, user_id))


async def active_drivers(session: AsyncSession, car_id: uuid.UUID) -> list[tuple[User, CarDriver]]:
    stmt = (
        select(User, CarDriver)
        .join(CarDriver, CarDriver.user_id == User.id)
        .where(CarDriver.car_id == car_id, CarDriver.revoked_at.is_(None))
        .order_by(CarDriver.linked_at, User.username)
    )
    return [(u, link) for u, link in (await session.execute(stmt)).all()]


async def cars_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[tuple[Car, CarDriver]]:
    stmt = (
        select(Car, CarDriver)
        .join(CarDriver, CarDriver.car_id == Car.id)
        .where(CarDriver.user_id == user_id, CarDriver.revoked_at.is_(None), Car.revoked_at.is_(None))
        .order_by(CarDriver.linked_at)
    )
    return [(c, link) for c, link in (await session.execute(stmt)).all()]


async def vin_taken(session: AsyncSession, vin: str, exclude_car: uuid.UUID | None = None) -> bool:
    stmt = select(func.count()).select_from(Car).where(Car.vin == vin)
    if exclude_car is not None:
        stmt = stmt.where(Car.id != exclude_car)
    return bool((await session.execute(stmt)).scalar_one())
