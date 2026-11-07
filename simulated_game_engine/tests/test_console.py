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
    assert "active:      phone_call" in out
    assert "configured:  eating_drinking, eyes_closed, eyes_off_road" in out
    await con.execute("stop phone_call")
    await con.execute("start sneezing")
    assert "does not configure 'sneezing'" in capsys.readouterr().out
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


async def test_end_with_an_action_does_not_end_the_drive(console, core) -> None:
    con, ft = console
    core.handle(start_msg())
    await con.execute("start eyes_off_road")
    with pytest.raises(ValueError, match="stop eyes_off_road"):
        await con.execute("end eyes_off_road")
    with pytest.raises(ValueError, match="takes no arguments"):
        await con.execute("status now")
    assert core.session is not None and "eyes_off_road" in core.active_actions
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
    await con.execute("start eyes_closed")
    await ft.sleep(1.0)                                      # CRITICAL, needs dismiss
    await con.execute("stop eyes_closed")
    await con.execute("status")
    assert "EYES_CLOSED CRITICAL latched" in capsys.readouterr().out
    await con.execute("dismiss")
    assert "dismissed 1 latched alert(s)" in capsys.readouterr().out
    assert ft.sent[-1]["type"] == p.ALERT_CLEAR
    assert core.status().alerts == {}
