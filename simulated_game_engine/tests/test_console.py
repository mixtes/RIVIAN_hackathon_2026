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
        await con.execute("start PHONE_CALL")
    core.handle(start_msg())
    await con.execute("start PHONE_CALL")
    await con.execute("status")
    out = capsys.readouterr().out
    assert "active:      PHONE_CALL" in out
    assert "configured:  EATING_DRINKING, EYES_CLOSED, EYES_OFF_ROAD" in out
    await con.execute("stop PHONE_CALL")
    await con.execute("start SNEEZING")
    assert "does not configure 'SNEEZING'" in capsys.readouterr().out
    await con.execute("end")
    assert [m["type"] for m in ft.sent] == [p.ALERT, p.ALERT_CLEAR, p.DRIVE_RESULT]
    await con.execute("drop")
    assert con.server.dropped
    assert await con.execute("quit") is False


async def test_bad_commands(console) -> None:
    con, _ = console
    for line in ("bogus", "pulse PHONE_CALL", "auto maybe", "scenario nope"):
        with pytest.raises(ValueError):
            await con.execute(line)


async def test_end_with_an_action_does_not_end_the_drive(console, core) -> None:
    con, ft = console
    core.handle(start_msg())
    await con.execute("start EYES_OFF_ROAD")
    with pytest.raises(ValueError, match="stop EYES_OFF_ROAD"):
        await con.execute("end EYES_OFF_ROAD")
    with pytest.raises(ValueError, match="takes no arguments"):
        await con.execute("status now")
    assert core.session is not None and "EYES_OFF_ROAD" in core.active_actions
    assert p.DRIVE_RESULT not in [m["type"] for m in ft.sent]


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


async def test_dismiss_command_dismisses_latched_alerts(console, core, clock, capsys) -> None:
    con, ft = console
    core.handle(start_msg())
    await con.execute("dismiss")
    assert "no latched alert showing" in capsys.readouterr().out
    await con.execute("start EYES_CLOSED")
    await ft.sleep(1.0)                                      # CRITICAL, needs dismiss
    await con.execute("stop EYES_CLOSED")
    await con.execute("status")
    assert "EYES_CLOSED CRITICAL latched" in capsys.readouterr().out
    await con.execute("dismiss")
    assert "dismissed 1 latched alert(s)" in capsys.readouterr().out
    assert ft.sent[-1]["type"] == p.ALERT_CLEAR
    assert core.status().alerts == {}
