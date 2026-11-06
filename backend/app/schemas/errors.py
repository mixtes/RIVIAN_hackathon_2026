"""Error body models, used to document error responses in OpenAPI (spec §8.2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str
    issue: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail]
    request_id: str | None


class ErrorResponse(BaseModel):
    error: ErrorInfo


_DESCRIPTIONS = {
    400: "Bad request",
    401: "Unauthenticated / invalid credentials / token expired or reused",
    403: "Forbidden / driver not linked / car revoked",
    404: "Not found",
    409: "Conflict",
    413: "Payload too large",
    422: "Validation error",
    429: "Too many failed attempts",
}


def ERRORS(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {c: {"model": ErrorResponse, "description": _DESCRIPTIONS.get(c, "Error")} for c in codes}
