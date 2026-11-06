"""Entry point: `python -m simulated_game_engine` / `sim-engine`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import random
import signal
import sys

from . import __version__
from .clock import SystemClock
from .core import BridgeCore
from .engine_loader import ENGINE_PATH_ENV, EngineLoadError, default_engine_path, load_engine
from .server import BridgeServer
from .simulator import FAST_SPEED, SCENARIOS, Simulator

log = logging.getLogger("simulated_game_engine")


def default_engine_version(engine_version: str | None) -> str:
    # "rhe" = RivianHackatonEngine. Kept short: the backend stores engine_version as VARCHAR(40).
    return f"rhe/{engine_version or 'unknown'}+bridge-{__version__}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="simulated_game_engine",
        description="WebSocket bridge around RivianHackatonEngine's SafetySystem, "
                    "with a driver-event simulator.")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    ap.add_argument("--path", default="/ws", help="WebSocket path (default /ws)")
    ap.add_argument("--engine-path", default=None,
                    help=f"RivianHackatonEngine folder (default {default_engine_path()}, "
                         f"or ${ENGINE_PATH_ENV})")
    ap.add_argument("--tick-ms", type=int, default=100, help="engine tick period (default 100)")
    ap.add_argument("--tick-mode", choices=("per-action", "engine"), default="per-action",
                    help="per-action (default): re-report each active action so every "
                         "escalation is delivered; engine: AlertEngine.tick(), which returns "
                         "only the most severe alert per tick")
    ap.add_argument("--engine-version", default=None,
                    help="engine_version reported in hello and drive.result")
    ap.add_argument("--no-cli", action="store_true",
                    help="headless: no interactive prompt (automatic when stdin is not a TTY)")
    ap.add_argument("--auto", action="store_true", help="start with auto mode on")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS),
                    help="run this scenario on every session the car starts")
    speed = ap.add_mutually_exclusive_group()
    speed.add_argument("--speed", type=float, default=1.0,
                       help="scenario speed factor for the clean-driving gaps (default 1)")
    speed.add_argument("--fast", action="store_const", dest="speed", const=FAST_SPEED,
                       help=f"same as --speed {FAST_SPEED:g} (action holds stay real-time)")
    ap.add_argument("--seed", type=int, default=None, help="random seed for auto mode")
    ap.add_argument("-v", "--verbose", action="store_true", help="log full JSON messages")
    return ap.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    try:
        engine = load_engine(args.engine_path)
    except EngineLoadError as exc:
        log.error("%s", exc)
        return 2
    core = BridgeCore(
        engine.new_system(), SystemClock(),
        engine_version=args.engine_version or default_engine_version(engine.version),
        tick_mode=args.tick_mode,
    )
    log.info("engine loaded from %s; configured actions: %s",
             engine.path, ", ".join(core.configured_actions))
    server = BridgeServer(core, host=args.host, port=args.port, path=args.path,
                          tick_interval_s=args.tick_ms / 1000)
    await server.start()
    sim = Simulator(core, server.dispatch, rng=random.Random(args.seed))
    if args.auto:
        sim.set_auto(True)
        log.info("auto mode on")
    if args.scenario:
        sim.start_scenario(args.scenario, speed=args.speed, every_session=True)

    interactive = not args.no_cli and sys.stdin.isatty()
    try:
        if interactive:
            from .console import Console  # prompt_toolkit only needed interactively
            await Console(core, server, sim).run()
        else:
            log.info("headless mode (no CLI); Ctrl-C or SIGTERM to stop")
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, stop.set)
            await stop.wait()
    finally:
        await sim.close()
        await server.close()
        if core.pending_results:
            log.warning("exiting with %d unacked drive result(s); they are lost",
                        len(core.pending_results))
    return 0


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(asyncio.run(run(args)))
