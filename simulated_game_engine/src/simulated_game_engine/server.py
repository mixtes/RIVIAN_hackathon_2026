"""WebSocket transport (websockets asyncio API) + the ticker task.

Single-client policy: the bridge serves one car. When a new client connects, the previous
connection is closed with code 4000 ("replaced by a newer connection") and the newest wins.
The session itself is not affected: it belongs to the bridge, and the new client resumes it
with `hello` + `session.start` (same `session_id`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Iterable
from http import HTTPStatus

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from . import protocol as p
from .core import BridgeCore

log = logging.getLogger("simulated_game_engine.server")

REPLACED_CLOSE_CODE = 4000


class BridgeServer:
    def __init__(self, core: BridgeCore, *, host: str = "0.0.0.0", port: int = 8765,
                 path: str = "/ws", tick_interval_s: float = 0.1,
                 ping_interval_s: float | None = 20) -> None:
        self.core = core
        self.host = host
        self.port = port
        self.path = path
        self.tick_interval_s = tick_interval_s
        self.ping_interval_s = ping_interval_s
        self._server: Server | None = None
        self._ticker: asyncio.Task[None] | None = None
        self._client: ServerConnection | None = None

    # ---- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        self._server = await serve(
            self._handler, self.host, self.port,
            process_request=self._process_request,
            ping_interval=self.ping_interval_s,
        )
        sock = next(iter(self._server.sockets))
        self.port = sock.getsockname()[1]  # real port when started with port=0
        self._ticker = asyncio.create_task(self._tick_loop(), name="bridge-ticker")
        log.info("listening on ws://%s:%d%s", self.host, self.port, self.path)

    async def close(self) -> None:
        if self._ticker:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def connected(self) -> bool:
        return self._client is not None

    @property
    def client_address(self) -> str | None:
        if self._client is None:
            return None
        addr = self._client.remote_address
        return f"{addr[0]}:{addr[1]}" if addr else "?"

    # ---- outbound --------------------------------------------------------------------------

    async def dispatch(self, messages: Iterable[p.Message]) -> None:
        """Send bridge-initiated messages (alerts, results) to the current client, if any.

        With no client connected, alerts are dropped (fire-and-forget, §8.4 rule 5);
        drive results stay in `core.pending_results` and are re-sent on the next `hello`.
        """
        messages = list(messages)
        if not messages:
            return
        client = self._client
        if client is None:
            for m in messages:
                log.info("(no client) not sent: %s", p.summarize(m))
            return
        await self._send(client, messages)

    async def drop_client(self) -> bool:
        """Close the current connection (CLI `drop`) to test reconnects."""
        client = self._client
        if client is None:
            return False
        await client.close(1001, "dropped by operator")
        return True

    async def _send(self, conn: ServerConnection, messages: list[p.Message]) -> None:
        for m in messages:
            log.info("→ %s", p.summarize(m))
            log.debug("→ %s", json.dumps(m))
            try:
                await conn.send(json.dumps(m))
            except ConnectionClosed:
                log.info("connection closed while sending %s", m.get("type"))
                return

    # ---- inbound ---------------------------------------------------------------------------

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.path.split("?", 1)[0] != self.path:
            return connection.respond(HTTPStatus.NOT_FOUND, f"Not found. Connect to {self.path}\n")
        return None

    async def _handler(self, conn: ServerConnection) -> None:
        previous = self._client
        self._client = conn
        log.info("client connected from %s", self.client_address)
        if previous is not None:
            log.info("closing previous client: replaced by a newer connection")
            await previous.close(REPLACED_CLOSE_CODE, "replaced by a newer connection")
        try:
            async for raw in conn:
                try:
                    obj = json.loads(raw)
                    log.info("← %s", p.summarize(obj) if isinstance(obj, dict) else "?")
                except ValueError:
                    pass
                log.debug("← %s", raw)
                try:
                    replies = self.core.handle_raw(raw)
                except Exception:  # engine bug etc.: keep the connection alive
                    log.exception("error while handling message")
                    replies = [p.envelope(p.ERROR, p.ErrorOut(
                        code=p.INTERNAL_ERROR, message="bridge failed to handle the message"),
                        self.core.clock.wall())]
                await self._send(conn, replies)
        except ConnectionClosed:
            pass
        finally:
            if self._client is conn:
                self._client = None
            log.info("client disconnected (code %s)", conn.close_code)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self.tick_interval_s)
            try:
                await self.dispatch(self.core.tick())
            except Exception:
                log.exception("tick failed")
