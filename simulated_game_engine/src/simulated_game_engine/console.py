"""Interactive operator CLI (prompt_toolkit). Only used when stdin is a TTY."""

from __future__ import annotations

import logging
import shlex

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.patch_stdout import patch_stdout

from .core import BridgeCore, NoActiveSessionError
from .server import BridgeServer
from .simulator import FAST_SPEED, SCENARIOS, Simulator

log = logging.getLogger("simulated_game_engine.cli")

HELP = """\
Commands:
  status                      connection, session, active/configured actions, pending results
  start <action>              begin a continuous action    (e.g. start phone_call)
  stop <action>               end it
  pulse <action> <seconds>    start now, stop automatically after N seconds
  auto on|off                 random driver behaviour during sessions
  scenario demo [fast|<speed>]  scripted ~2 min drive (waits for a session), then end
  scenario stop               cancel a running scenario
  end                         end the whole drive now and send drive.result
  drop                        close the car's connection (to test reconnects)
  help                        this text
  quit                        stop the bridge
Actions are engine keys, e.g. phone_call, eyes_off_road (case-insensitive)."""


NO_ARG_COMMANDS = frozenset({"help", "?", "quit", "exit", "status", "end", "drop"})


class Console:
    def __init__(self, core: BridgeCore, server: BridgeServer, sim: Simulator) -> None:
        self.core = core
        self.server = server
        self.sim = sim

    async def run(self) -> None:
        words = ["status", "start", "stop", "pulse", "auto", "on", "off", "scenario",
                 *SCENARIOS, "fast", "end", "drop", "help", "quit", *self.core.configured_actions]
        prompt: PromptSession[str] = PromptSession(
            completer=WordCompleter(words, ignore_case=True))
        print(HELP)
        with patch_stdout(raw=True):
            while True:
                try:
                    line = await prompt.prompt_async("bridge> ")
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    return
                if not line.strip():
                    continue
                try:
                    if await self.execute(line) is False:
                        return
                except NoActiveSessionError as exc:
                    print(f"! {exc}")
                except (ValueError, RuntimeError) as exc:
                    print(f"! {exc}")

    async def execute(self, line: str) -> bool | None:
        """Run one command. Returns False to quit."""
        args = shlex.split(line)
        cmd, rest = args[0].lower(), args[1:]
        if cmd in NO_ARG_COMMANDS and rest:
            # Never ignore extra words: `end eyes_off_road` used to end the whole drive.
            hint = (f"; to stop an action use 'stop {rest[0]}'"
                    if cmd == "end" and self.core.is_configured(rest[0]) else "")
            raise ValueError(f"'{cmd}' takes no arguments{hint}")
        match cmd:
            case "help" | "?":
                print(HELP)
            case "quit" | "exit":
                return False
            case "status":
                self._status()
            case "start" | "stop":
                action = self._action(rest, cmd)
                await self.sim.set_action(action, cmd == "start")
            case "pulse":
                if len(rest) != 2:
                    raise ValueError("usage: pulse <action> <seconds>")
                action = self._action(rest[:1], cmd)
                self.sim.pulse(action, float(rest[1]))
            case "auto":
                if not rest or rest[0] not in ("on", "off"):
                    raise ValueError("usage: auto on|off")
                self.sim.set_auto(rest[0] == "on")
                print(f"auto mode {'on' if self.sim.auto_enabled else 'off'}")
            case "scenario":
                self._scenario(rest)
            case "end":
                await self.sim.end()
            case "drop":
                print("connection closed" if await self.server.drop_client() else "no client")
            case _:
                raise ValueError(f"unknown command {cmd!r} (try 'help')")
        return None

    def _action(self, rest: list[str], cmd: str) -> str:
        if len(rest) != 1:
            raise ValueError(f"usage: {cmd} <action>")
        action = rest[0].lower()
        if not self.core.is_configured(action):
            print(f"! warning: the engine does not configure {action!r}; it will ignore it "
                  f"(configured: {', '.join(self.core.configured_actions)})")
        return action

    def _scenario(self, rest: list[str]) -> None:
        if rest and rest[0] == "stop":
            print("scenario stopped" if self.sim.stop_scenario() else "no scenario running")
            return
        if not rest:
            raise ValueError(f"usage: scenario {'|'.join(SCENARIOS)} [fast|<speed>] | stop")
        speed = 1.0
        if len(rest) > 1:
            speed = FAST_SPEED if rest[1] == "fast" else float(rest[1])
        self.sim.start_scenario(rest[0], speed=speed)
        if self.core.session is None:
            print("scenario will start when the car starts a session")

    def _status(self) -> None:
        st = self.core.status()
        print(f"client:      {self.server.client_address or 'not connected'}")
        if st.session:
            s = st.session
            print(f"session:     {s.session_id} ({s.display_name or s.user_id}), "
                  f"started {s.wall_start:%H:%M:%S}Z")
        else:
            print("session:     none")
        print(f"active:      {', '.join(st.active_actions) or '-'}")
        print(f"alert ids:   {st.alert_ids or '-'}")
        print(f"configured:  {', '.join(st.configured_actions)}")
        print(f"unacked:     {', '.join(st.pending_results) or '-'}")
        print(f"auto mode:   {'on' if self.sim.auto_enabled else 'off'}; "
              f"scenario {'running' if self.sim.scenario_running else 'idle'}")
