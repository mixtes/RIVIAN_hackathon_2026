"""Car provisioning and car ↔ driver links (spec §7.5, §8.5)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.config import Settings
from app.errors import Conflict, NotFound
from app.models import Car, CarDriver, User
from app.repositories import cars as cars_repo
from app.schemas.cars import CarRegisterRequest, CarUpdateRequest
from app.schemas.users import RegisterRequest
from app.security.tokens import generate_car_token, hash_token
from app.services.accounts import AccountService


class CarDriverLimit(Conflict):
    code = "CAR_DRIVER_LIMIT"
    message = "This car already has the maximum number of drivers"


class VinTaken(Conflict):
    code = "VIN_TAKEN"
    message = "A car with this VIN is already registered"


@dataclass
class CarService:
    session: AsyncSession
    settings: Settings
    clock: Clock

    async def register(self, req: CarRegisterRequest) -> tuple[Car, str]:
        if req.vin and await cars_repo.vin_taken(self.session, req.vin):
            raise VinTaken(details=[{"field": "vin", "issue": "taken"}])
        token = generate_car_token()
        car = Car(
            name=req.name.strip(),
            model=req.model,
            vin=req.vin,
            token_hash=hash_token(token),
            created_at=self.clock.now(),
            last_seen_at=self.clock.now(),
        )
        self.session.add(car)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise VinTaken(details=[{"field": "vin", "issue": "taken"}]) from exc
        return car, token

    async def update(self, car: Car, req: CarUpdateRequest) -> Car:
        changes = req.model_dump(exclude_unset=True)
        if changes.get("name") is None:
            changes.pop("name", None)  # name is NOT NULL; null means "leave as is"
        if changes.get("vin") and await cars_repo.vin_taken(self.session, changes["vin"], exclude_car=car.id):
            raise VinTaken(details=[{"field": "vin", "issue": "taken"}])
        for key, value in changes.items():
            setattr(car, key, value.strip() if key == "name" else value)
        await self.session.commit()
        return car

    async def drivers(self, car: Car) -> list[tuple[User, CarDriver]]:
        return await cars_repo.active_drivers(self.session, car.id)

    async def _link(self, car: Car, user: User) -> tuple[CarDriver, bool]:
        """Create or reactivate the link. Returns (link, newly_linked). Caller commits."""
        await cars_repo.lock_car(self.session, car.id)
        link = await cars_repo.get_link(self.session, car.id, user.id)
        if link is not None and link.revoked_at is None:
            return link, False
        if await cars_repo.active_driver_count(self.session, car.id) >= self.settings.max_drivers_per_car:
            raise CarDriverLimit()
        now = self.clock.now()
        if link is None:
            link = CarDriver(car_id=car.id, user_id=user.id, linked_at=now)
            self.session.add(link)
            return link, True
        link.revoked_at = None
        link.linked_at = now
        return link, False

    async def link_existing(
        self, car: Car, accounts: AccountService, username: str, password: str
    ) -> tuple[User, CarDriver, bool]:
        user = await accounts.authenticate(username, password)
        link, created = await self._link(car, user)
        await self.session.commit()
        return user, link, created

    async def register_driver(
        self, car: Car, accounts: AccountService, req: RegisterRequest
    ) -> tuple[User, CarDriver]:
        # The driver limit is checked BEFORE the user is created (spec §8.5).
        await cars_repo.lock_car(self.session, car.id)
        if await cars_repo.active_driver_count(self.session, car.id) >= self.settings.max_drivers_per_car:
            raise CarDriverLimit()
        user = await accounts.create_user(req)
        link = CarDriver(car_id=car.id, user_id=user.id, linked_at=self.clock.now())
        self.session.add(link)
        await self.session.commit()
        return user, link

    async def unlink(self, car: Car, user_id: uuid.UUID) -> None:
        link = await cars_repo.get_link(self.session, car.id, user_id)
        if link is None:
            raise NotFound("This driver is not linked to this car")
        if link.revoked_at is None:
            link.revoked_at = self.clock.now()
            await self.session.commit()

    async def cars_for_user(self, user: User) -> list[tuple[Car, CarDriver]]:
        return await cars_repo.cars_for_user(self.session, user.id)

    async def revoke_car_for_user(self, user: User, car_id: uuid.UUID) -> None:
        link = await cars_repo.get_link(self.session, car_id, user.id)
        if link is None:
            raise NotFound("This car is not linked to you")
        if link.revoked_at is None:
            link.revoked_at = self.clock.now()
            await self.session.commit()

    def touch(self, car: Car, link: CarDriver | None, now: datetime) -> None:
        car.last_seen_at = now
        if link is not None:
            link.last_used_at = now
