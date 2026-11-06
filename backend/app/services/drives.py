"""Drive upload (idempotent), history and detail (spec §8.6, §8.7)."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import LeaderboardCache
from app.clock import Clock
from app.errors import BadRequest, Conflict, NotFound, ValidationFailed
from app.models import Car, CarDriver, Drive, DriveEvent, DriveMistake, User
from app.repositories import cars as cars_repo
from app.repositories import drives as drives_repo
from app.schemas.drives import (
    CarRef,
    DriveDetail,
    DriveListItem,
    DrivePage,
    DriveUpload,
    EventOut,
    MistakeOut,
    MistakesSummary,
)

MAX_CLOCK_SKEW = timedelta(minutes=10)


class DriveConflict(Conflict):
    code = "DRIVE_CONFLICT"
    message = "A different drive with this id already exists"


def _dec(value: float | None, places: str) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _f(value: Decimal | float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def encode_cursor(ended_at: datetime, drive_id: uuid.UUID) -> str:
    raw = json.dumps({"e": ended_at.isoformat(), "i": str(drive_id)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        ended = datetime.fromisoformat(data["e"])
        if ended.tzinfo is None:
            raise ValueError("naive")
        return ended, uuid.UUID(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise BadRequest("Invalid cursor", details=[{"field": "cursor", "issue": "malformed"}]) from exc


@dataclass
class DriveService:
    session: AsyncSession
    clock: Clock

    # ---- upload ----------------------------------------------------------------

    async def upload(
        self,
        *,
        user: User,
        car: Car,
        body: DriveUpload,
        raw: dict[str, Any],
        cache: LeaderboardCache,
    ) -> tuple[DriveDetail, bool]:
        """Returns (drive, created). created=False means an identical re-upload (200)."""
        now = self.clock.now()
        if body.ended_at > now + MAX_CLOCK_SKEW:
            raise ValidationFailed(details=[{"field": "ended_at", "issue": "is in the future"}])
        if body.user_id is not None and body.user_id != user.id:
            raise ValidationFailed(details=[{"field": "user_id", "issue": "does not match X-Driver-Id"}])

        existing = await drives_repo.get(self.session, body.id)
        if existing is not None:
            return await self._existing(existing, user, car, raw), False

        duration_s = int((body.ended_at - body.started_at).total_seconds())
        drive = Drive(
            id=body.id,
            user_id=user.id,
            car_id=car.id,
            started_at=body.started_at,
            ended_at=body.ended_at,
            duration_s=duration_s,
            distance_km=_dec(body.distance_km, "0.001"),
            score=_dec(body.score, "0.01"),
            engine_version=body.engine_version,
            raw_payload=raw,
            received_at=now,
        )
        mistakes = [
            DriveMistake(
                drive_id=body.id,
                type=m.type,
                count=m.count,
                total_duration_s=_dec(m.total_duration_s, "0.01"),
                max_duration_s=_dec(m.max_duration_s, "0.01"),
            )
            for m in body.mistakes
        ]
        events = [
            DriveEvent(
                drive_id=body.id, type=e.type, started_at=e.started_at, duration_s=_dec(e.duration_s, "0.01")
            )
            for e in body.events
        ]
        drives_repo.add(self.session, drive, mistakes, events)
        link: CarDriver | None = await cars_repo.get_link(self.session, car.id, user.id)
        car.last_seen_at = now
        if link is not None:
            link.last_used_at = now
        try:
            await self.session.commit()
        except IntegrityError:
            # A concurrent upload with the same id won the race.
            await self.session.rollback()
            existing = await drives_repo.get(self.session, body.id)
            if existing is None:
                raise
            return await self._existing(existing, user, car, raw), False

        cache.invalidate_region(user.region_code)
        detail = await self.detail(user, body.id)
        return detail, True

    async def _existing(self, existing: Drive, user: User, car: Car, raw: dict[str, Any]) -> DriveDetail:
        if existing.user_id != user.id or existing.car_id != car.id or existing.raw_payload != raw:
            raise DriveConflict()
        return await self.detail(user, existing.id)

    # ---- read ------------------------------------------------------------------

    async def list(
        self, user: User, *, limit: int, cursor: str | None, car_id: uuid.UUID | None
    ) -> DrivePage:
        after = decode_cursor(cursor) if cursor else None
        rows = await drives_repo.list_page(
            self.session, user_id=user.id, limit=limit + 1, car_id=car_id, after=after
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            DriveListItem(
                id=r.id,
                car=CarRef(id=r.car_id, name=r.car_name),
                started_at=r.started_at,
                ended_at=r.ended_at,
                duration_s=r.duration_s,
                score=round(r.score, 2),
                mistakes_summary=MistakesSummary(total_count=r.total_mistakes, types=r.mistake_types),
            )
            for r in rows
        ]
        next_cursor = encode_cursor(rows[-1].ended_at, rows[-1].id) if has_more and rows else None
        return DrivePage(items=items, next_cursor=next_cursor)

    async def detail(self, user: User, drive_id: uuid.UUID) -> DriveDetail:
        drive = await drives_repo.get(self.session, drive_id)
        if drive is None or drive.user_id != user.id:
            raise NotFound("Drive not found")
        parts = await drives_repo.detail_parts(self.session, drive_id)
        assert parts is not None
        car, mistakes, events = parts
        return DriveDetail(
            id=drive.id,
            car=CarRef(id=car.id, name=car.name),
            started_at=drive.started_at,
            ended_at=drive.ended_at,
            duration_s=drive.duration_s,
            score=float(drive.score),
            mistakes_summary=MistakesSummary(
                total_count=sum(m.count for m in mistakes),
                types=[m.type for m in mistakes if m.count > 0],
            ),
            distance_km=None if drive.distance_km is None else float(drive.distance_km),
            engine_version=drive.engine_version,
            received_at=drive.received_at,
            mistakes=[
                MistakeOut(
                    type=m.type,
                    count=m.count,
                    total_duration_s=_f(m.total_duration_s),
                    max_duration_s=_f(m.max_duration_s),
                )
                for m in mistakes
            ],
            events=[
                EventOut(type=e.type, started_at=e.started_at, duration_s=_f(e.duration_s)) for e in events
            ],
        )
