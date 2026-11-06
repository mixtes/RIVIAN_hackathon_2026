from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest

from simulated_game_engine import protocol as p
from simulated_game_engine.core import NoActiveSessionError
from simulated_game_engine.simulator import (
    DEMO_SCENARIO,
    FAST_SPEED,
    Simulator,
    scenario_duration,
)

from .conftest import of_type, start_msg


class FakeTime:
    """`sleep` that advances the fake clock in ticks, running the core's ticker."""

    def __init__(self, core, clock, step: float = 0.1) -> None:
        self.core, self.clock, self.step = core, clock, step
        self.sent: list[dict[str, Any]] = []

    async def dispatch(self, messages: list[dict[str, Any]]) -> None:
        self.sent.extend(messages)

    async def sleep(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 1e-9:
            dt = min(self.step, remaining)
            self.clock.advance(dt)
            remaining -= dt
            self.sent.extend(self.core.tick())
        await asyncio.sleep(0)


def make_sim(core, clock, **kw) -> tuple[Simulator, FakeTime]:
    ft = FakeTime(core, clock)
    return Simulator(core, ft.dispatch, sleep=ft.sleep, **kw), ft


def test_demo_scenario_is_about_two_minutes() -> None:
    assert 110 <= scenario_duration(DEMO_SCENARIO) <= 130
    assert scenario_duration(DEMO_SCENARIO, FAST_SPEED) < 25


@pytest.mark.parametrize("speed", [1.0, FAST_SPEED])
async def test_demo_scenario_sequence(core, clock, speed) -> None:
    sim, ft = make_sim(core, clock)
    core.handle(start_msg())
    t0 = clock.monotonic()
    assert await sim.run_scenario(DEMO_SCENARIO, speed=speed) is True

    alerts = [(m["payload"]["type"], m["payload"]["level"]) for m in of_type(ft.sent, p.ALERT)]
    distinct = [a for i, a in enumerate(alerts) if i == 0 or a != alerts[i - 1]]  # no keep-alives
    assert distinct == [
        ("PHONE_CALL", "VISUAL"), ("PHONE_CALL", "AUDIO"),
        ("EYES_OFF_ROAD", "AUDIO"), ("EYES_OFF_ROAD", "HAPTIC"), ("EYES_OFF_ROAD", "CRITICAL"),
        ("EYES_CLOSED", "CRITICAL"),                   # latched, kept alive until the end
    ]
    # phone + eyes-off-road clear when they stop; the undismissed latched one at drive end
    assert len(of_type(ft.sent, p.ALERT_CLEAR)) == 3
    assert ft.sent[-1]["type"] == p.DRIVE_RESULT
    result = ft.sent[-1]["payload"]
    counts = {m["type"]: m["count"] for m in result["mistakes"] if m["count"]}
    assert counts == {"PHONE_CALL": 1, "EYES_OFF_ROAD": 1, "EYES_CLOSED": 1}
    assert 0 < result["score"] < 100
    assert core.session is None
    assert clock.monotonic() - t0 == pytest.approx(scenario_duration(DEMO_SCENARIO, speed), abs=0.5)


async def test_scenario_waits_for_session_and_aborts_when_it_ends(core, clock) -> None:
    sim, ft = make_sim(core, clock)
    task = sim.start_scenario("demo", speed=FAST_SPEED)
    for _ in range(5):                       # waiting: no session yet
        await asyncio.sleep(0)
    assert sim.scenario_running and core.session is None and ft.sent == []

    started = clock.monotonic()
    core.handle(start_msg())
    plain_sleep = ft.sleep
    ended_by_car: list[dict[str, Any]] = []

    async def sleep_then_car_ends(seconds: float) -> None:
        await plain_sleep(seconds)
        # the car ends the drive 5 s in, during the scenario's phone call
        if core.session is not None and clock.monotonic() - started >= 5.0:
            ended_by_car.extend(core.end_session("MANUAL"))

    sim.sleep = sleep_then_car_ends
    await asyncio.wait_for(task, 5)
    assert of_type(ended_by_car, p.DRIVE_RESULT)
    assert of_type(ft.sent, p.DRIVE_RESULT) == []        # scenario didn't end it again
    assert ("EYES_OFF_ROAD" not in
            {m["payload"]["type"] for m in of_type(ft.sent, p.ALERT)})  # aborted early


async def test_pulse_starts_and_stops(core, clock) -> None:
    sim, ft = make_sim(core, clock)
    with pytest.raises(NoActiveSessionError):
        sim.pulse("phone_call", 1.0)
    core.handle(start_msg())
    await sim.pulse("phone_call", 3.5)
    assert [m["type"] for m in ft.sent] == [p.ALERT, p.ALERT, p.ALERT_CLEAR]
    assert "phone_call" not in core.active_actions


async def test_auto_mode_generates_actions(core, clock) -> None:
    sim, ft = make_sim(core, clock, rng=random.Random(1),
                       auto_gap_s=(1.0, 2.0), auto_hold_s=(4.5, 5.0))
    core.handle(start_msg())
    sim.set_auto(True)
    assert sim.auto_enabled
    for _ in range(300):
        await asyncio.sleep(0)
        if len(of_type(ft.sent, p.ALERT_CLEAR)) >= 2:
            break
    sim.set_auto(False)
    await sim.close()
    assert not sim.auto_enabled
    assert len(of_type(ft.sent, p.ALERT_CLEAR)) >= 2
    assert {m["payload"]["type"] for m in of_type(ft.sent, p.ALERT)} <= {
        a.upper() for a in core.configured_actions}
