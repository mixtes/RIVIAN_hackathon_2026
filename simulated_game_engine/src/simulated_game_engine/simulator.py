"""Driver-event simulator: stands in for the camera/sensor detection layer.

Generates `on_event(action, is_active)` calls on the bridge core: manual start/stop, timed
pulses, random auto mode, and scripted scenarios. Everything is async and takes an
injectable `sleep`, so tests can drive it with a fake clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from . import protocol as p
from .core import BridgeCore, NoActiveSessionError

log = logging.getLogger("simulated_game_engine.sim")

Dispatch = Callable[[list[p.Message]], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Wait:
    """Clean driving. Scaled by the scenario speed factor."""
    seconds: float


@dataclass(frozen=True)
class Hold:
    """Hold an action for N seconds. NOT scaled: the engine's escalation thresholds are real
    seconds, so shortening a hold would change which alert levels are reached."""
    action: str
    seconds: float


@dataclass(frozen=True)
class End:
    pass


Step = Wait | Hold | End

# ~2 minutes. Engine thresholds (RivianHackatonEngine 0.3.0 defaults):
#   PHONE_CALL:    VISUAL at 0 s, AUDIO at 3 s (HAPTIC at 6 s is not reached)
#   EYES_OFF_ROAD: AUDIO at 1 s, HAPTIC at 2.5 s, CRITICAL at 4 s
#   EYES_CLOSED:   CRITICAL at 0.5 s, LATCHED: stays after the eyes open until the driver
#                  presses Dismiss in the app (or the drive ends)
DEMO_SCENARIO: tuple[Step, ...] = (
    Wait(15.0),
    Hold("PHONE_CALL", 4.5),       # VISUAL -> AUDIO -> clear
    Wait(20.0),
    Hold("EYES_OFF_ROAD", 5.0),    # AUDIO -> HAPTIC -> CRITICAL -> clear
    Wait(15.0),
    Hold("EYES_CLOSED", 2.0),      # CRITICAL, needs Dismiss (no clear when the eyes open)
    Wait(58.5),                    # clean stretch
    End(),
)
SCENARIOS: dict[str, Sequence[Step]] = {"demo": DEMO_SCENARIO}

FAST_SPEED = 10.0


def scenario_duration(steps: Sequence[Step], speed: float = 1.0) -> float:
    return sum(s.seconds / speed if isinstance(s, Wait) else
               s.seconds if isinstance(s, Hold) else 0.0 for s in steps)


class Simulator:
    def __init__(self, core: BridgeCore, dispatch: Dispatch, *,
                 sleep: Sleep = asyncio.sleep, rng: random.Random | None = None,
                 auto_gap_s: tuple[float, float] = (15.0, 45.0),
                 auto_hold_s: tuple[float, float] = (0.5, 7.0)) -> None:
        self.core = core
        self.dispatch = dispatch
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.auto_gap_s = auto_gap_s
        self.auto_hold_s = auto_hold_s
        self._auto_task: asyncio.Task[None] | None = None
        self._scenario_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- manual events ---------------------------------------------------------------------

    async def set_action(self, action: str, active: bool) -> None:
        """Raises NoActiveSessionError when no drive is active."""
        await self.dispatch(self.core.set_action(action, active))

    async def end(self) -> None:
        await self.dispatch(self.core.end_session())

    async def dismiss_all(self) -> int:
        """Dismiss every latched alert. Returns how many were dismissed."""
        msgs = self.core.dismiss_all()
        await self.dispatch(msgs)
        return sum(1 for m in msgs if m["type"] == p.ALERT_CLEAR)

    def pulse(self, action: str, seconds: float) -> asyncio.Task[None]:
        """Start `action` now (raises if no session) and stop it after `seconds`."""
        if self.core.session is None:
            raise NoActiveSessionError("no active session")
        return self._spawn(self._hold(action, seconds, self.core.session.session_id))

    async def _hold(self, action: str, seconds: float, session_id: str) -> None:
        await self.set_action(action, True)
        await self.sleep(seconds)
        if self._same_session(session_id) and action.strip().upper() in self.core.active_actions:
            await self.set_action(action, False)

    # ---- auto mode -------------------------------------------------------------------------

    @property
    def auto_enabled(self) -> bool:
        return self._auto_task is not None and not self._auto_task.done()

    def set_auto(self, enabled: bool) -> None:
        if enabled and not self.auto_enabled:
            self._auto_task = self._spawn(self._auto_loop())
        elif not enabled and self._auto_task is not None:
            self._auto_task.cancel()
            self._auto_task = None

    async def _auto_loop(self) -> None:
        while True:
            await self.sleep(self.rng.uniform(*self.auto_gap_s))
            session = self.core.session
            if session is None:
                continue
            candidates = [a for a in self.core.configured_actions
                          if a not in self.core.active_actions]
            if not candidates:
                continue
            action = self.rng.choice(candidates)
            hold = round(self.rng.uniform(*self.auto_hold_s), 1)
            log.info("auto: %s for %.1fs", action, hold)
            try:
                await self._hold(action, hold, session.session_id)
            except NoActiveSessionError:
                continue

    # ---- scenarios -------------------------------------------------------------------------

    @property
    def scenario_running(self) -> bool:
        return self._scenario_task is not None and not self._scenario_task.done()

    def start_scenario(self, name: str, *, speed: float = 1.0,
                       every_session: bool = False) -> asyncio.Task[None]:
        """Run a scenario in the background. It waits for a session if none is active.

        `every_session=True` (headless `--scenario`) runs it again for each new session.
        """
        if name not in SCENARIOS:
            raise ValueError(f"unknown scenario {name!r} (known: {', '.join(SCENARIOS)})")
        if self.scenario_running:
            raise RuntimeError("a scenario is already running")
        steps = SCENARIOS[name]

        async def runner() -> None:
            while True:
                await self.run_scenario(steps, speed=speed, name=name)
                if not every_session:
                    return
                await self._wait_until_no_session()

        self._scenario_task = self._spawn(runner())
        return self._scenario_task

    def stop_scenario(self) -> bool:
        if self.scenario_running and self._scenario_task is not None:
            self._scenario_task.cancel()
            return True
        return False

    async def run_scenario(self, steps: Sequence[Step], *, speed: float = 1.0,
                           name: str = "scenario") -> bool:
        """Run steps against the current (or next) session. Returns False if it was aborted
        because the session ended or changed underneath it."""
        if speed <= 0:
            raise ValueError("speed must be > 0")
        session_id = await self._wait_for_session()
        log.info("scenario %s: starting on session %s (~%.0fs)", name, session_id[:8],
                 scenario_duration(steps, speed))
        try:
            for step in steps:
                if not self._same_session(session_id):
                    log.info("scenario %s: session ended, aborting", name)
                    return False
                if isinstance(step, Wait):
                    await self.sleep(step.seconds / speed)
                elif isinstance(step, Hold):
                    log.info("scenario %s: %s for %.1fs", name, step.action, step.seconds)
                    await self._hold(step.action, step.seconds, session_id)
                elif isinstance(step, End):
                    log.info("scenario %s: ending the drive", name)
                    await self.end()
        except NoActiveSessionError:
            log.info("scenario %s: session ended, aborting", name)
            return False
        log.info("scenario %s: done", name)
        return True

    async def _wait_for_session(self) -> str:
        announced = False
        while self.core.session is None:
            if not announced:
                log.info("waiting for the car to start a session (session.start)…")
                announced = True
            await self.sleep(0.1)
        return self.core.session.session_id

    async def _wait_until_no_session(self) -> None:
        while self.core.session is not None:
            await self.sleep(0.1)

    def _same_session(self, session_id: str) -> bool:
        return self.core.session is not None and self.core.session.session_id == session_id

    # ---- housekeeping ----------------------------------------------------------------------

    def _spawn(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            if isinstance(exc, NoActiveSessionError):
                log.info("%s", exc)
            else:
                log.error("simulator task failed", exc_info=exc)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
