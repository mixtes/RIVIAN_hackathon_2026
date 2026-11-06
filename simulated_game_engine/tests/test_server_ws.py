"""Real WebSocket round trips against the bridge on an ephemeral port (real clock)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from simulated_game_engine import protocol as p
from simulated_game_engine.clock import SystemClock
from simulated_game_engine.core import BridgeCore
from simulated_game_engine.server import REPLACED_CLOSE_CODE, BridgeServer

from .conftest import ENGINE_VERSION, inbound


@pytest.fixture
async def bridge(engine) -> AsyncIterator[BridgeServer]:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    server = BridgeServer(core, host="127.0.0.1", port=0, tick_interval_s=0.02)
    await server.start()
    yield server
    await server.close()


def url(server: BridgeServer) -> str:
    return f"ws://127.0.0.1:{server.port}/ws"


async def send(ws: ClientConnection, msg_type: str, **payload: Any) -> None:
    await ws.send(json.dumps(inbound(msg_type, **payload)))


async def recv(ws: ClientConnection, timeout: float = 5.0) -> dict[str, Any]:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def recv_until(ws: ClientConnection, msg_type: str, timeout: float = 8.0) -> list[dict]:
    """Collect messages until one of `msg_type` arrives (inclusive)."""
    got: list[dict[str, Any]] = []
    async with asyncio.timeout(timeout):
        while True:
            got.append(await recv(ws, timeout))
            if got[-1]["type"] == msg_type:
                return got


async def hello(ws: ClientConnection) -> dict[str, Any]:
    await send(ws, p.HELLO, app_version="1.0.0", car_id="car-1", protocol=1)
    reply = await recv(ws)
    assert reply["type"] == p.HELLO
    return reply


async def test_full_round_trip(bridge: BridgeServer) -> None:
    sid, uid = str(uuid.uuid4()), str(uuid.uuid4())
    async with connect(url(bridge)) as ws:
        h = await hello(ws)
        assert h["payload"]["active_session_id"] is None
        assert h["payload"]["supported_actions"] == ["EYES_OFF_ROAD", "PHONE_CALL"]

        await send(ws, p.SESSION_START, session_id=sid, user_id=uid, display_name="Mihajlo",
                   car_id="car-1", started_at="2026-11-06T08:00:00Z")
        assert (await recv(ws))["type"] == p.SESSION_ACK

        # driver event -> alert, escalation via the ticker, then clear
        await bridge.dispatch(bridge.core.set_action("eyes_off_road", True))
        alerts = []
        async with asyncio.timeout(6):
            while len(alerts) < 3:
                m = await recv(ws)
                assert m["type"] == p.ALERT
                alerts.append(m["payload"])
        assert [a["level"] for a in alerts] == ["AUDIO", "HAPTIC", "CRITICAL"]
        assert len({a["alert_id"] for a in alerts}) == 1
        await bridge.dispatch(bridge.core.set_action("eyes_off_road", False))
        clear = await recv(ws)
        assert clear["type"] == p.ALERT_CLEAR
        assert clear["payload"]["alert_id"] == alerts[0]["alert_id"]

        await send(ws, p.SESSION_END, session_id=sid, reason="MANUAL")
        result = await recv(ws)
        assert result["type"] == p.DRIVE_RESULT
        payload = result["payload"]
        assert (payload["session_id"], payload["user_id"]) == (sid, uid)
        assert payload["mistakes"] and payload["score"] < 100
        await send(ws, p.DRIVE_RESULT_ACK, session_id=sid)
        await asyncio.sleep(0.1)
        assert not bridge.core.pending_results


async def test_reconnect_resumes_session_and_redelivers_unacked_result(bridge) -> None:
    sid, uid = str(uuid.uuid4()), str(uuid.uuid4())
    async with connect(url(bridge)) as ws:
        await hello(ws)
        await send(ws, p.SESSION_START, session_id=sid, user_id=uid)
        assert (await recv(ws))["type"] == p.SESSION_ACK
        await bridge.dispatch(bridge.core.set_action("phone_call", True))
        first = await recv(ws)
        assert first["payload"]["level"] == "VISUAL"
    # connection lost mid-drive
    await asyncio.sleep(0.1)
    assert not bridge.connected and bridge.core.session is not None

    async with connect(url(bridge)) as ws:
        h = await hello(ws)
        assert h["payload"]["active_session_id"] == sid
        await send(ws, p.SESSION_START, session_id=sid, user_id=uid)
        msgs = await recv_until(ws, p.SESSION_ACK)
        assert msgs[-1]["payload"]["session_id"] == sid
        # active alert is re-sent with the same id after resumption
        resent = await recv(ws)
        assert resent["type"] == p.ALERT
        assert resent["payload"]["alert_id"] == first["payload"]["alert_id"]
        await send(ws, p.SESSION_END, session_id=sid)
        result = (await recv_until(ws, p.DRIVE_RESULT))[-1]
        assert result["payload"]["session_id"] == sid
        # drop without acking

    async with connect(url(bridge)) as ws:
        await send(ws, p.HELLO, protocol=1)
        h = await recv(ws)
        assert h["payload"]["active_session_id"] is None
        again = await recv(ws)                     # re-delivered right after hello
        assert again["type"] == p.DRIVE_RESULT
        assert again["payload"] == result["payload"]
        await send(ws, p.DRIVE_RESULT_ACK, session_id=sid)

    async with connect(url(bridge)) as ws:
        await hello(ws)
        with pytest.raises(TimeoutError):
            await recv(ws, timeout=0.3)            # nothing more to deliver


async def test_newest_client_wins(bridge) -> None:
    async with connect(url(bridge)) as old:
        await hello(old)
        async with connect(url(bridge)) as new:
            await hello(new)
            with pytest.raises(ConnectionClosed) as exc_info:
                await recv(old)
            assert exc_info.value.rcvd is not None
            assert exc_info.value.rcvd.code == REPLACED_CLOSE_CODE
            assert bridge.connected
            await hello(new)                        # new connection still works


async def test_wrong_path_is_404(bridge) -> None:
    with pytest.raises(InvalidStatus) as exc_info:
        async with connect(f"ws://127.0.0.1:{bridge.port}/nope"):
            pass
    assert exc_info.value.response.status_code == 404


async def test_bad_message_gets_error_and_connection_survives(bridge) -> None:
    async with connect(url(bridge)) as ws:
        await ws.send("this is not json")
        err = await recv(ws)
        assert err["type"] == p.ERROR and err["payload"]["code"] == p.BAD_REQUEST
        await hello(ws)


async def test_alerts_without_client_are_dropped_results_are_kept(bridge) -> None:
    sid = str(uuid.uuid4())
    bridge.core.handle(inbound(p.SESSION_START, session_id=sid, user_id=str(uuid.uuid4())))
    await bridge.dispatch(bridge.core.set_action("phone_call", True))   # nobody listening
    await bridge.dispatch(bridge.core.end_session())
    async with connect(url(bridge)) as ws:
        await hello(ws)
        result = await recv(ws)
        assert result["type"] == p.DRIVE_RESULT and result["payload"]["session_id"] == sid
