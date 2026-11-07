"""Entry point: `python -m simulated_game_engine` / `sim-engine`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import random
import signal
import sys
from pathlib import Path

from . import __version__
from .clock import SystemClock
from .core import BridgeCore
from .cv_source import CvSource, CvStartError, default_cv_python
from .engine_loader import ENGINE_PATH_ENV, EngineLoadError, default_engine_path, load_engine
from .server import BridgeServer
from .simulator import FAST_SPEED, SCENARIOS, Simulator

log = logging.getLogger("simulated_game_engine")


# simulated_game_engine/logs/bridge.log (gitignored: *.log)
DEFAULT_LOG_FILE = Path(__file__).resolve().parents[2] / "logs" / "bridge.log"


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
    cv = ap.add_argument_group(
        "real detection (CVEngine)",
        "Play a video (or a camera) through RivianHackatonEngine/CVEngine/live.py as the driver: "
        "the video starts when the car app starts a drive, its alerts go to the app, and the "
        "drive ends when the video ends.")
    cv.add_argument("--cv", metavar="VIDEO", default=None,
                    help="video file, or a camera index such as 0")
    cv.add_argument("--cv-python", default=None,
                    help="Python with CVEngine's packages (default: <engine>/.venv, else this one)")
    cv.add_argument("--cv-stride", type=int, default=6,
                    help="analyze at most every Nth frame (default 6); the video always plays at "
                         "normal speed and the analysis skips frames it cannot keep up with")
    cv.add_argument("--cv-light", action="store_true",
                    help="pose-only hands and head (faster on CPU, no mediapipe needed)")
    cv.add_argument("--cv-no-phone", action="store_true",
                    help="skip phone detection (PHONE_USE): about twice as many analyses per second")
    cv.add_argument("--cv-no-hands", action="store_true",
                    help="skip hands-off-wheel detection and wheel calibration (faster start-up)")
    cv.add_argument("--cv-no-yawn", action="store_true", help="skip yawn detection (YAWN)")
    cv.add_argument("--cv-pose-model", default=None,
                    help="pose weights for live.py, e.g. yolo11s-pose.pt (faster, same results in our tests)")
    cv.add_argument("--cv-phone-model", default=None, help="phone detector weights for live.py (default yolo11m.pt)")
    cv.add_argument("--cv-keep-drive", action="store_true",
                    help="don't end the drive when the video ends (end it in the app)")
    cv.add_argument("--cv-no-window", action="store_true", help="don't show the video window")
    cv.add_argument("--cv-play-resumed", action="store_true",
                    help="also play the video for a drive the app resumes on reconnect (by default "
                         "only drives started after the bridge started play it)")
    ap.add_argument("-v", "--verbose", action="store_true", help="log full JSON messages")
    ap.add_argument("--log-file", default=str(DEFAULT_LOG_FILE),
                    help=f"also write the log to this file, replaced on every start "
                         f"(default {DEFAULT_LOG_FILE}; '' = no file)")
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
        detected_actions=engine.detected_actions,
    )
    log.info("engine %s loaded from %s; configured actions: %s; detected: %s",
             engine.version or "?", engine.path, ", ".join(core.configured_actions),
             ", ".join(core.detected_actions or ["(not declared)"]))
    server = BridgeServer(core, host=args.host, port=args.port, path=args.path,
                          tick_interval_s=args.tick_ms / 1000)
    await server.start()
    sim = Simulator(core, server.dispatch, rng=random.Random(args.seed))
    if args.auto:
        sim.set_auto(True)
        log.info("auto mode on")
    if args.scenario:
        sim.start_scenario(args.scenario, speed=args.speed, every_session=True)
    cv_source = None
    if args.cv:
        extra = ["--stride", str(max(1, args.cv_stride))]
        if args.cv_light:
            extra += ["--method", "pose", "--head", "pose"]
        if args.cv_no_phone:
            extra.append("--no-phone")
        if args.cv_no_hands:
            extra.append("--no-hands")
        if args.cv_no_yawn:
            extra.append("--no-yawn")
        if args.cv_pose_model:
            extra += ["--pose-model", args.cv_pose_model]
        if args.cv_phone_model:
            extra += ["--phone-model", args.cv_phone_model]
        cv_source = CvSource(
            core, server.dispatch, source=args.cv, cv_dir=engine.path / "CVEngine",
            python=args.cv_python or default_cv_python(engine.path), extra_args=extra,
            end_drive_when_done=not args.cv_keep_drive, play=not args.cv_no_window,
            play_resumed=args.cv_play_resumed)
        try:
            cv_source.start()
        except CvStartError as exc:
            log.error("%s", exc)
            await server.close()
            return 2

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
        if cv_source is not None:
            await cv_source.close()
        await sim.close()
        await server.close()
        if core.pending_results:
            log.warning("exiting with %d unacked drive result(s); they are lost",
                        len(core.pending_results))
    return 0


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, mode="w", encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(asyncio.run(run(args)))
