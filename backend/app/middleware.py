"""Pure-ASGI middleware: request id, body-size limit, access log, last-resort 500 handler."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import error_body
from app.request_context import set_request_id

access_log = logging.getLogger("app.access")
error_log = logging.getLogger("app.errors")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


def _header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k.lower() == name:
            return str(v.decode("latin-1"))
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int, hsts: bool = False) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, b"x-request-id")
        request_id = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        set_request_id(request_id)
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id

        started = time.perf_counter()
        status_holder = {"status": 500}
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                if self.hsts:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message["headers"] = headers
            await send(message)

        async def send_json(status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode()
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(payload)).encode()),
                    ],
                }
            )
            await send_wrapper({"type": "http.response.body", "body": payload})

        try:
            receive_fn = receive
            content_length = _header(scope, b"content-length")
            if content_length is not None and content_length.isdigit():
                if int(content_length) > self.max_body_bytes:
                    await send_json(413, error_body("PAYLOAD_TOO_LARGE", "Request body too large"))
                    return
            elif scope.get("method") in {"POST", "PUT", "PATCH"}:
                # No (valid) Content-Length: buffer the body with a hard cap, then replay it.
                chunks: list[bytes] = []
                total = 0
                more = True
                while more:
                    msg = await receive()
                    if msg["type"] != "http.request":
                        break
                    body = msg.get("body", b"")
                    total += len(body)
                    if total > self.max_body_bytes:
                        await send_json(413, error_body("PAYLOAD_TOO_LARGE", "Request body too large"))
                        return
                    chunks.append(body)
                    more = msg.get("more_body", False)
                buffered = b"".join(chunks)
                replayed = False

                async def replay() -> Message:
                    nonlocal replayed
                    if not replayed:
                        replayed = True
                        return {"type": "http.request", "body": buffered, "more_body": False}
                    return await receive()

                receive_fn = replay

            await self.app(scope, receive_fn, send_wrapper)
        except Exception:
            error_log.exception("unhandled error", extra={"request_id": request_id})
            if not response_started:
                await send_json(500, error_body("INTERNAL_ERROR", "Internal server error"))
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            access_log.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_holder["status"],
                    "latency_ms": latency_ms,
                    "request_id": request_id,
                    "principal": state.get("principal_type"),
                },
            )
            set_request_id(None)
