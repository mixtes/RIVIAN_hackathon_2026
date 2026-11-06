from __future__ import annotations

import uuid
from typing import Any

import pytest

from simulated_game_engine import protocol as p
from simulated_game_engine.clock import FakeClock
from simulated_game_engine.core import BridgeCore
from simulated_game_engine.engine_loader import EngineInfo, load_engine

ENGINE_VERSION = "rhe/unknown+bridge-test"

# The engine's 8 canonical actions, sorted (hello.supported_actions without DETECTED_ACTIONS).
ALL_ACTIONS = ["EATING_DRINKING", "EYES_CLOSED", "EYES_OFF_ROAD", "HANDS_OFF_WHEEL",
               "PHONE_CALL", "PHONE_USE", "SMOKING", "YAWN"]


@pytest.fixture(scope="session")
def engine() -> EngineInfo:
    return load_engine()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def core(engine: EngineInfo, clock: FakeClock) -> BridgeCore:
    return BridgeCore(engine.new_system(), clock, engine_version=ENGINE_VERSION)


def inbound(msg_type: str, **payload: Any) -> dict[str, Any]:
    return {"v": 1, "type": msg_type, "msg_id": str(uuid.uuid4()),
            "ts": "2026-11-06T08:00:00.000Z", "payload": payload}


def start_msg(session_id: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    return inbound(p.SESSION_START, session_id=session_id or str(uuid.uuid4()),
                   user_id=user_id or str(uuid.uuid4()), display_name="Mihajlo",
                   car_id=str(uuid.uuid4()), started_at="2026-11-06T08:00:00Z")


def of_type(messages: list[dict[str, Any]], msg_type: str) -> list[dict[str, Any]]:
    return [m for m in messages if m["type"] == msg_type]


def run_for(core: BridgeCore, clock: FakeClock, seconds: float,
            step: float = 0.1) -> list[dict[str, Any]]:
    """Advance time in `step`s, calling tick() like the server's ticker does."""
    out: list[dict[str, Any]] = []
    for _ in range(round(seconds / step)):
        clock.advance(step)
        out.extend(core.tick())
    return out
