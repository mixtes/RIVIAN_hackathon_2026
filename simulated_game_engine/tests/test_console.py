from __future__ import annotations

import pytest

from simulated_game_engine import protocol as p
from simulated_game_engine.console import Console
from simulated_game_engine.core import NoActiveSessionError

from .conftest import start_msg
from .test_simulator import make_sim


class FakeServer:
    client_address = "127.0.0.1:1"

    def __init__(self) -> None:
        self.dropped = False

    async def drop_client(self) -> bool:
        self.dropped = True
        return True


@pytest.fixture
def console(core, clock):
    sim, ft = make_sim(core, clock)
    return Console(core, FakeServer(), sim), ft  # type: ignore[arg-type]


async def test_commands(console, core, capsys) -> None:
    con, ft = console
    with pytest.raises(NoActiveSessionError):
        await con.execute("start phone_call")
    core.handle(start_msg())
    await con.execute("start PHONE_CALL")
    await con.execute("status")
    out = capsys.readouterr().out
    assert "active:      phone_call" in out and "configured:  eyes_off_road, phone_call" in out
    await con.execute("stop phone_call")
    await con.execute("start smoking")
    assert "does not configure 'smoking'" in capsys.readouterr().out
    await con.execute("end")
    assert [m["type"] for m in ft.sent] == [p.ALERT, p.ALERT_CLEAR, p.DRIVE_RESULT]
    await con.execute("drop")
    assert con.server.dropped
    assert await con.execute("quit") is False


async def test_bad_commands(console) -> None:
    con, _ = console
    for line in ("bogus", "pulse phone_call", "auto maybe", "scenario nope"):
        with pytest.raises(ValueError):
            await con.execute(line)


async def test_auto_and_scenario_toggle(console, capsys) -> None:
    con, _ = console
    await con.execute("auto on")
    assert con.sim.auto_enabled
    await con.execute("auto off")
    assert not con.sim.auto_enabled
    await con.execute("scenario demo fast")
    assert con.sim.scenario_running
    assert "when the car starts a session" in capsys.readouterr().out
    await con.execute("scenario stop")
    await con.sim.close()
