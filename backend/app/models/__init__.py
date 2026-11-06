"""SQLAlchemy ORM models (spec §6). The Alembic migration is the source of truth for DDL."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(40), nullable=False)
    region_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    avatar_color: Mapped[str] = mapped_column(CHAR(7), nullable=False, server_default="#4F7DF3")
    leaderboard_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (Index("ix_users_region", "region_code"),)


class Car(Base):
    __tablename__ = "cars"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    vin: Mapped[str | None] = mapped_column(String(17), unique=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str | None] = mapped_column(String(60))
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class CarDriver(Base):
    __tablename__ = "car_drivers"

    car_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cars.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    linked_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (Index("ix_car_drivers_user", "user_id"),)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    user_agent: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_refresh_tokens_user", "user_id"),)


class Drive(Base):
    __tablename__ = "drives"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    car_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cars.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String(40))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("duration_s >= 0", name="ck_drives_duration"),
        CheckConstraint("distance_km >= 0", name="ck_drives_distance"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_drives_score"),
        CheckConstraint("ended_at >= started_at", name="ck_drives_times"),
        Index("ix_drives_user_ended", "user_id", text("ended_at DESC")),
        Index("ix_drives_ended", "ended_at"),
        Index("ix_drives_car", "car_id", text("ended_at DESC")),
    )


class DriveMistake(Base):
    __tablename__ = "drive_mistakes"

    drive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drives.id", ondelete="CASCADE"), primary_key=True
    )
    type: Mapped[str] = mapped_column(String(40), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_duration_s: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_duration_s: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    __table_args__ = (
        CheckConstraint("count >= 0", name="ck_drive_mistakes_count"),
        CheckConstraint("total_duration_s >= 0", name="ck_drive_mistakes_total"),
        CheckConstraint("max_duration_s >= 0", name="ck_drive_mistakes_max"),
        Index("ix_drive_mistakes_type", "type"),
    )


class DriveEvent(Base):
    __tablename__ = "drive_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    drive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drives.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    duration_s: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    __table_args__ = (Index("ix_drive_events_drive", "drive_id", "started_at"),)


__all__ = ["Base", "Car", "CarDriver", "Drive", "DriveEvent", "DriveMistake", "RefreshToken", "User"]
