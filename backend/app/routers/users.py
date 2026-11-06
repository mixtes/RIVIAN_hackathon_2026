from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status

from app.cache import LeaderboardCache
from app.deps import get_leaderboard_cache
from app.models import User
from app.routers._deps import account_service, car_service
from app.schemas.errors import ERRORS
from app.schemas.users import ChangePasswordRequest, LinkedCar, UpdateProfileRequest, UserProfile
from app.security.principals import Actor, get_actor, get_user_principal
from app.services.accounts import AccountService
from app.services.cars import CarService

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserProfile,
    responses=ERRORS(401, 403),
    summary="Own profile (user or driver-via-car)",
)
async def me(actor: Actor = Depends(get_actor)) -> UserProfile:
    return UserProfile.model_validate(actor.user)


@router.patch(
    "/me", response_model=UserProfile, responses=ERRORS(401, 403, 422), summary="Update own profile"
)
async def update_me(
    body: UpdateProfileRequest,
    user: User = Depends(get_user_principal),
    svc: AccountService = Depends(account_service),
    cache: LeaderboardCache = Depends(get_leaderboard_cache),
) -> UserProfile:
    return UserProfile.model_validate(await svc.update_profile(user, body, cache))


@router.put(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=ERRORS(401, 403, 422),
    summary="Change password (revokes all refresh tokens)",
)
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_user_principal),
    svc: AccountService = Depends(account_service),
) -> Response:
    await svc.change_password(user, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me/cars", response_model=list[LinkedCar], responses=ERRORS(401, 403), summary="Cars linked to me"
)
async def my_cars(
    user: User = Depends(get_user_principal), svc: CarService = Depends(car_service)
) -> list[LinkedCar]:
    return [
        LinkedCar(
            car_id=c.id, name=c.name, model=c.model, linked_at=link.linked_at, last_used_at=link.last_used_at
        )
        for c, link in await svc.cars_for_user(user)
    ]


@router.delete(
    "/me/cars/{car_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=ERRORS(401, 403, 404),
    summary="Revoke a car's access to me",
)
async def revoke_car(
    car_id: uuid.UUID, user: User = Depends(get_user_principal), svc: CarService = Depends(car_service)
) -> Response:
    await svc.revoke_car_for_user(user, car_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
