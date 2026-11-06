"""Error hierarchy and the uniform error body (spec §8.2)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.request_context import current_request_id

log = logging.getLogger("app.errors")


class AppError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or []
        self.headers = headers
        super().__init__(self.message)


class BadRequest(AppError):
    status_code = 400
    code = "BAD_REQUEST"
    message = "Bad request"


class Unauthenticated(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"
    message = "Authentication required"


class InvalidCredentials(Unauthenticated):
    code = "INVALID_CREDENTIALS"
    message = "Invalid username or password"


class TokenExpired(Unauthenticated):
    code = "TOKEN_EXPIRED"
    message = "Token expired"


class TokenReused(Unauthenticated):
    code = "TOKEN_REUSED"
    message = "Refresh token was already used; please log in again"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "Forbidden"


class DriverNotLinked(Forbidden):
    code = "DRIVER_NOT_LINKED"
    message = "This driver is not linked to this car"


class CarRevoked(Forbidden):
    code = "CAR_REVOKED"
    message = "This car has been revoked"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Not found"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Conflict"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    message = "Request body too large"


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "Request validation failed"


class TooManyAttempts(AppError):
    status_code = 429
    code = "TOO_MANY_ATTEMPTS"
    message = "Too many failed attempts; try again later"


def error_body(code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "request_id": current_request_id(),
        }
    }


def error_response(
    status: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(code, message, details), headers=headers)


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_ATTEMPTS",
}


def _loc_to_field(loc: tuple[Any, ...] | list[Any]) -> str:
    parts = [str(p) for p in loc]
    if parts and parts[0] in {"body", "query", "path", "header", "cookie"}:
        parts = parts[1:]
    return ".".join(parts) or "body"


def validation_details(errors: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for err in errors:
        msg = str(err.get("msg", "invalid"))
        # pydantic prefixes custom ValueError messages with "Value error, "
        msg = msg.removeprefix("Value error, ")
        out.append({"field": _loc_to_field(err.get("loc", ())), "issue": msg})
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            422, "VALIDATION_ERROR", "Request validation failed", validation_details(exc.errors())
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").capitalize()
        return error_response(exc.status_code, code, message, headers=getattr(exc, "headers", None))
