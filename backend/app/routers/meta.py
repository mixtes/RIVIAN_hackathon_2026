from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.mistakes import KNOWN_MISTAKE_TYPES
from app.domain.regions import all_regions

router = APIRouter(tags=["meta"])


class MistakeTypeOut(BaseModel):
    type: str
    label: str
    has_duration: bool


class RegionOut(BaseModel):
    code: str
    name: str


class Health(BaseModel):
    status: str
    db: str


@router.get("/health", response_model=Health, summary="Liveness, including a DB check")
async def health(request: Request, session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "unavailable"})
    return JSONResponse(content={"status": "ok", "db": "ok"})


@router.get("/api/v1/meta/mistake-types", response_model=list[MistakeTypeOut], summary="Known mistake types")
async def mistake_types() -> list[MistakeTypeOut]:
    return [
        MistakeTypeOut(type=m.type, label=m.label, has_duration=m.has_duration) for m in KNOWN_MISTAKE_TYPES
    ]


@router.get("/api/v1/meta/regions", response_model=list[RegionOut], summary="Selectable regions (ISO 3166-1)")
async def regions() -> list[RegionOut]:
    return [RegionOut(code=c, name=n) for c, n in all_regions()]
