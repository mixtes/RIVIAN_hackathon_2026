"""Per-request context (request id, principal type) carried in contextvars for logging and errors."""

from __future__ import annotations

from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_principal_type: ContextVar[str | None] = ContextVar("principal_type", default=None)


def current_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def current_principal_type() -> str | None:
    return _principal_type.get()


def set_principal_type(value: str | None) -> None:
    _principal_type.set(value)
