from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status

from app.routers._deps import account_service, car_service
from app.schemas.cars import (
    CarInfo,
    CarRegisterRequest,
    CarRegisterResponse,
    CarUpdateRequest,
    LinkDriverRequest,
    LinkDriverResponse,
    LinkedDriver,
)
from app.schemas.errors import ERRORS
from app.schemas.users import RegisterRequest, UserProfile
from app.security.principals import CarPrincipal, get_car_principal, require_provisioning_key
from app.services.accounts import AccountService
from app.services.cars import CarService

router = APIRouter(prefix="/api/v1/cars", tags=["cars"])


async def _car_info(svc: CarService, principal: CarPrincipal) -> CarInfo:
    car = principal.car
    drivers = [
        LinkedDriver(
            **UserProfile.model_validate(u).model_dump(),
            linked_at=link.linked_at,
            last_used_at=link.last_used_at,
        )
        for u, link in await svc.drivers(car)
    ]
    return CarInfo(car_id=car.id, name=car.name, model=car.model, vin=car.vin, drivers=drivers)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=CarRegisterResponse,
    responses=ERRORS(401, 409, 422),
    dependencies=[Depends(require_provisioning_key)],
    summary="Register a car (returns the car token exactly once)",
)
async def register_car(
    body: CarRegisterRequest, svc: CarService = Depends(car_service)
) -> CarRegisterResponse:
    car, token = await svc.register(body)
    return CarRegisterResponse(car_id=car.id, car_token=token, name=car.name)


@router.get("/me", response_model=CarInfo, responses=ERRORS(401, 403), summary="Car info and active drivers")
async def get_car(
    principal: CarPrincipal = Depends(get_car_principal), svc: CarService = Depends(car_service)
) -> CarInfo:
    return await _car_info(svc, principal)


@router.patch(
    "/me", response_model=CarInfo, responses=ERRORS(401, 403, 409, 422), summary="Update name, model, VIN"
)
async def update_car(
    body: CarUpdateRequest,
    principal: CarPrincipal = Depends(get_car_principal),
    svc: CarService = Depends(car_service),
) -> CarInfo:
    await svc.update(principal.car, body)
    return await _car_info(svc, principal)


@router.post(
    "/me/drivers",
    response_model=LinkDriverResponse,
    status_code=status.HTTP_200_OK,
    responses={
        201: {"model": LinkDriverResponse, "description": "Newly linked"},
        **ERRORS(401, 403, 409, 422, 429),
    },
    summary="Link an existing driver (username + password)",
)
async def link_driver(
    body: LinkDriverRequest,
    response: Response,
    principal: CarPrincipal = Depends(get_car_principal),
    cars: CarService = Depends(car_service),
    accounts: AccountService = Depends(account_service),
) -> LinkDriverResponse:
    user, link, created = await cars.link_existing(principal.car, accounts, body.username, body.password)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return LinkDriverResponse(user=UserProfile.model_validate(user), linked_at=link.linked_at)


@router.post(
    "/me/drivers/register",
    status_code=status.HTTP_201_CREATED,
    response_model=LinkDriverResponse,
    responses=ERRORS(401, 403, 409, 422),
    summary="Create an account on the car and link it",
)
async def register_driver(
    body: RegisterRequest,
    principal: CarPrincipal = Depends(get_car_principal),
    cars: CarService = Depends(car_service),
    accounts: AccountService = Depends(account_service),
) -> LinkDriverResponse:
    user, link = await cars.register_driver(principal.car, accounts, body)
    return LinkDriverResponse(user=UserProfile.model_validate(user), linked_at=link.linked_at)


@router.delete(
    "/me/drivers/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=ERRORS(401, 403, 404),
    summary="Unlink a driver (soft revoke)",
)
async def unlink_driver(
    user_id: uuid.UUID,
    principal: CarPrincipal = Depends(get_car_principal),
    svc: CarService = Depends(car_service),
) -> Response:
    await svc.unlink(principal.car, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
