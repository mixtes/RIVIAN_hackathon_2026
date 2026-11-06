"""Wire protocol models. Source of truth: car_frontend/frontend_specification.md §8."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1

# Message types (§8.3)
HELLO = "hello"
SESSION_START = "session.start"
SESSION_ACK = "session.ack"
SESSION_END = "session.end"
DRIVE_RESULT = "drive.result"
DRIVE_RESULT_ACK = "drive.result.ack"
ALERT = "alert"
ALERT_CLEAR = "alert.clear"
ALERT_DISMISS = "alert.dismiss"  # app -> engine: the driver dismissed a latched alert
ERROR = "error"

# Error codes sent by the bridge
UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
BAD_REQUEST = "BAD_REQUEST"
UNKNOWN_TYPE = "UNKNOWN_TYPE"
NO_ACTIVE_SESSION = "NO_ACTIVE_SESSION"
SESSION_ENDED = "SESSION_ENDED"
INTERNAL_ERROR = "INTERNAL_ERROR"

# §8.3 session.end reasons sent by the app, plus ENGINE for drives the bridge ends itself
# (CLI `end`, scenario). Unknown reasons from the app are accepted and passed through.
END_REASONS = ("MANUAL", "DRIVER_SWITCH", "IGNITION_OFF", "APP_SHUTDOWN")
ENGINE_END_REASON = "ENGINE"

# Default max display time per level (frontend spec §9.1)
DEFAULT_TTL_S: dict[str, int] = {"VISUAL": 8, "AUDIO": 8, "HAPTIC": 10, "CRITICAL": 15}


def iso(dt: datetime) -> str:
    """ISO-8601 UTC with milliseconds and a `Z` suffix, e.g. 2026-11-06T08:01:12.345Z."""
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Envelope(_In):
    v: int
    type: str
    msg_id: str | None = None
    ts: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# ---- App -> Engine payloads --------------------------------------------------------------

class HelloIn(_In):
    app_version: str | None = None
    car_id: str | None = None
    protocol: int = PROTOCOL_VERSION


class SessionStartIn(_In):
    session_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str | None = None
    car_id: str | None = None
    started_at: datetime | None = None  # informational; the bridge records its own start time


class SessionEndIn(_In):
    session_id: uuid.UUID
    reason: str = "MANUAL"


class DriveResultAckIn(_In):
    session_id: uuid.UUID


class AlertDismissIn(_In):
    alert_id: str
    session_id: str | None = None


# ---- Engine -> App payloads --------------------------------------------------------------

class HelloOut(BaseModel):
    engine_version: str
    active_session_id: str | None
    supported_actions: list[str]


class SessionAckOut(BaseModel):
    session_id: str


class AlertOut(BaseModel):
    alert_id: str
    session_id: str
    type: str
    level: Literal["VISUAL", "AUDIO", "HAPTIC", "CRITICAL"]
    message: str
    duration_so_far_s: float
    started_at: str
    ttl_s: int
    requires_ack: bool = False  # latched: stays until the driver dismisses it in the app


class AlertClearOut(BaseModel):
    alert_id: str


class ErrorOut(BaseModel):
    code: str
    message: str
    ref_msg_id: str | None = None


class MistakeOut(BaseModel):
    type: str
    count: int
    total_duration_s: float | None
    max_duration_s: float | None


class EventOut(BaseModel):
    type: str
    started_at: str
    duration_s: float | None


class DriveResultOut(BaseModel):
    """§8.5. Identical to the backend upload body minus `id`, plus `session_id`/`user_id`."""

    session_id: str
    user_id: str
    started_at: str
    ended_at: str
    distance_km: float | None = None
    score: float
    engine_version: str
    mistakes: list[MistakeOut]
    events: list[EventOut]
    extra: dict[str, Any] = Field(default_factory=dict)


Message = dict[str, Any]


def envelope(msg_type: str, payload: BaseModel | dict[str, Any], now: datetime) -> Message:
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return {
        "v": PROTOCOL_VERSION,
        "type": msg_type,
        "msg_id": str(uuid.uuid4()),
        "ts": iso(now),
        "payload": body,
    }


def summarize(msg: Message) -> str:
    """One-line human summary of a message, for logs and the CLI."""
    t = msg.get("type", "?")
    p = msg.get("payload") or {}
    if t == ALERT:
        ack = " [needs dismiss]" if p.get("requires_ack") else ""
        return (f"alert {p.get('type')} {p.get('level')} ({p.get('duration_so_far_s')}s) "
                f"\"{p.get('message')}\" id={_short(p.get('alert_id'))}{ack}")
    if t == ALERT_CLEAR:
        return f"alert.clear id={_short(p.get('alert_id'))}"
    if t == ALERT_DISMISS:
        return f"alert.dismiss id={_short(p.get('alert_id'))}"
    if t == DRIVE_RESULT:
        counts = {m["type"]: m["count"] for m in p.get("mistakes", []) if m.get("count")}
        return f"drive.result session={_short(p.get('session_id'))} score={p.get('score')} {counts}"
    if t == ERROR:
        return f"error {p.get('code')}: {p.get('message')}"
    if "session_id" in p:
        return f"{t} session={_short(p.get('session_id'))}"
    if t == HELLO and "engine_version" in p:
        return f"hello active_session={_short(p.get('active_session_id'))}"
    return t


def _short(value: Any) -> str:
    return str(value)[:8] if value else "-"
