from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.cache import LeaderboardCache
from app.deps import get_leaderboard_cache
from app.errors import BadRequest
from app.routers._deps import drive_service
from app.schemas.drives import DriveDetail, DrivePage, DriveUpload
from app.schemas.errors import ERRORS
from app.security.principals import Actor, get_actor, get_driver_principal
from app.services.drives import DriveService

router = APIRouter(prefix="/api/v1/drives", tags=["drives"])


@router.post(
    "",
    response_model=DriveDetail,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": DriveDetail, "description": "Identical drive already uploaded (idempotent success)"},
        **ERRORS(400, 401, 403, 409, 413, 422),
    },
    summary="Upload a drive (idempotent on id)",
)
async def upload_drive(
    body: DriveUpload,
    request: Request,
    response: Response,
    actor: Actor = Depends(get_driver_principal),
    svc: DriveService = Depends(drive_service),
    cache: LeaderboardCache = Depends(get_leaderboard_cache),
) -> DriveDetail:
    raw: Any = await request.json()  # stored verbatim as raw_payload (forward compatibility)
    if not isinstance(raw, dict):  # pragma: no cover - pydantic already rejected non-objects
        raise BadRequest("Body must be a JSON object")
    assert actor.car is not None
    drive, created = await svc.upload(user=actor.user, car=actor.car, body=body, raw=raw, cache=cache)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return drive


@router.get(
    "", response_model=DrivePage, responses=ERRORS(400, 401, 403), summary="My drive history (all cars)"
)
async def list_drives(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
    car_id: uuid.UUID | None = Query(None),
    actor: Actor = Depends(get_actor),
    svc: DriveService = Depends(drive_service),
) -> DrivePage:
    return await svc.list(actor.user, limit=limit, cursor=cursor, car_id=car_id)


@router.get(
    "/{drive_id}", response_model=DriveDetail, responses=ERRORS(401, 403, 404), summary="Drive detail"
)
async def get_drive(
    drive_id: uuid.UUID, actor: Actor = Depends(get_actor), svc: DriveService = Depends(drive_service)
) -> DriveDetail:
    return await svc.detail(actor.user, drive_id)
