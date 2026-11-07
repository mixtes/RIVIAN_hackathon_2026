"""Real detection as the driver: CVEngine (`RivianHackatonEngine/CVEngine/live.py`) on a video.

Lifecycle, repeated for every drive:
  1. start live.py with `--stream --wait --play --realtime` and let it load its models (READY)
  2. wait until the car app starts a NEW drive (session.start). A drive the app resumes on
     reconnect (it started before this bridge did) is skipped: end it in the app, then unlock.
  3. send "go": the plain video window opens and plays in real time
  4. every record "timestamp,flags" on its stdout -> BridgeCore.on_frame(flags) -> alerts to the app
  5. the video ends (or q in the window) -> the bridge ends the drive (drive.result, reason ENGINE)
     the app ends the drive first -> live.py is stopped
  6. back to 1 (models are pre-loaded again, ready for the next drive)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, timedelta
from pathlib import Path

from . import protocol as p
from .core import BridgeCore, NoActiveSessionError, Session

log = logging.getLogger("simulated_game_engine.cv")

Dispatch = Callable[[Iterable[p.Message]], Awaitable[None]]

# The CVEngine byte, highest bit first (CV_ENGINE_INTERFACE.md section 3); only used for logging.
CV_BITS = ((0x80, "EYES_CLOSED"), (0x40, "EYES_OFF_ROAD"), (0x20, "HANDS_OFF_WHEEL"),
           (0x10, "PHONE_USE"), (0x08, "YAWN"))


# stderr lines from live.py that are logged at DEBUG only.
_CV_NOISE = ("[t=", "W0000", "I0000", "E0000", "INFO: Created TensorFlow",
             "WARNING: All log messages before absl")


# A drive whose app start time is this much before the bridge started counts as resumed
# (slack for the emulator's clock being a little off from the host's).
RESUMED_SLACK_S = 10.0


class CvStartError(RuntimeError):
    pass


def default_cv_python(engine_dir: Path) -> str:
    """The engine's own venv (it has CVEngine's packages), else this interpreter."""
    for candidate in (engine_dir / ".venv" / "Scripts" / "python.exe",
                      engine_dir / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def parse_record(line: str) -> tuple[float, int] | None:
    """'12.346,32' or '12.346,32,1' (with the occupant count) -> (12.346, 32).

    The time is the frame's video time in seconds (from the start of the video, or of the
    camera stream). The occupant count is not part of the engine interface.
    """
    cells = line.strip().split(",")
    if len(cells) not in (2, 3):
        return None
    try:
        return float(cells[0]), int(cells[1])
    except ValueError:
        return None


class CvSource:
    def __init__(self, core: BridgeCore, dispatch: Dispatch, *, source: str, cv_dir: Path,
                 python: str, extra_args: list[str] | None = None,
                 end_drive_when_done: bool = True, play: bool = True,
                 play_resumed: bool = False) -> None:
        self.core = core
        self.dispatch = dispatch
        self.source = source if source.isdigit() else str(Path(source).resolve())
        self.cv_dir = cv_dir
        self.python = python
        self.extra_args = list(extra_args or [])
        self.end_drive_when_done = end_drive_when_done
        self.play = play
        self.play_resumed = play_resumed
        self.started_wall = core.clock.wall()    # drives the app started before this are resumed
        self._skipped: str | None = None           # resumed drive we already said we skip
        self._mono_go = 0.0                        # bridge clock when the video started
        self.state = "stopped"          # for `status`: loading / ready / playing / stopped
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None

    def command(self) -> list[str]:
        cmd = [self.python, "-u", str(self.cv_dir / "live.py"), self.source, "--stream", "--wait"]
        if self.play:
            cmd.append("--play")
        if not self.source.isdigit():
            cmd.append("--realtime")
        return cmd + self.extra_args

    def check(self) -> None:
        if not (self.cv_dir / "live.py").is_file():
            raise CvStartError(f"CVEngine not found: {self.cv_dir / 'live.py'}")
        if not self.source.isdigit() and not Path(self.source).is_file():
            raise CvStartError(f"video not found: {self.source}")
        if not hasattr(self.core.system, "on_frame"):
            raise CvStartError("the engine has no SafetySystem.on_frame (needs engine >= 0.4.0)")

    def start(self) -> asyncio.Task[None]:
        self.check()
        self._task = asyncio.ensure_future(self._run())
        return self._task

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await self._stop_process()

    # ---- the loop ----------------------------------------------------------------------------

    async def _run(self) -> None:
        log.info("CV source: %s (python %s)", self.source, self.python)
        while True:
            try:
                await self._one_drive()
            except CvStartError as exc:
                log.error("CV source stopped: %s", exc)
                self.state = "stopped"
                return
            # Before loading again, let a drive that is still active (kept on purpose) finish.
            while self.core.session is not None:
                await asyncio.sleep(0.2)

    async def _one_drive(self) -> None:
        self.state = "loading"
        log.info("CV: loading models (live.py) ...")
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.command(), cwd=str(self.cv_dir), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        except OSError as exc:
            raise CvStartError(f"cannot start {self.python}: {exc}") from exc
        self._proc = proc
        ready = asyncio.Event()
        stderr_task = asyncio.ensure_future(self._read_stderr(proc, ready))
        try:
            await self._wait_ready(proc, ready)
            self.state = "ready"
            log.info("CV: ready. Start a drive in the car app to play the video.")
            session_id = await self._wait_for_new_drive()
            assert proc.stdin is not None
            # live.py starts its video clock right after reading "go": a record at video time v
            # happened at go + v on the bridge clock. Using that instead of the arrival time
            # keeps durations exact when records are processed late or in bursts.
            self._mono_go = self.core.clock.monotonic()
            proc.stdin.write(b"go\n")
            await proc.stdin.drain()
            self.state = "playing"
            log.info("CV: drive %s started, playing the video", session_id[:8])
            finished = await self._stream(proc, session_id)
            if finished and self._same_session(session_id):
                if self.end_drive_when_done:
                    log.info("CV: video finished, ending the drive")
                    with contextlib.suppress(NoActiveSessionError):
                        await self.dispatch(self.core.end_session())
                else:
                    log.info("CV: video finished; the drive stays active until the app ends it")
        finally:
            self.state = "stopped"
            await self._stop_process()
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task

    async def _wait_for_new_drive(self) -> str:
        while True:
            s = self.core.session
            if s is not None and s.session_id != self._skipped:
                if self.play_resumed or not self.is_resumed(s):
                    return s.session_id
                self._skipped = s.session_id
                log.warning("CV: drive %s was started before the bridge (the app resumed it), "
                            "so the video won't play for it. End it in the app (or type 'end' "
                            "here), then unlock to start a new drive.", s.session_id[:8])
            await asyncio.sleep(0.1)

    def is_resumed(self, s: Session) -> bool:
        started = s.app_started_at
        if started is None:
            return False
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return started < self.started_wall - timedelta(seconds=RESUMED_SLACK_S)

    async def _wait_ready(self, proc: asyncio.subprocess.Process, ready: asyncio.Event) -> None:
        exited = asyncio.ensure_future(proc.wait())
        got_ready = asyncio.ensure_future(ready.wait())
        await asyncio.wait({exited, got_ready}, return_when=asyncio.FIRST_COMPLETED)
        if not ready.is_set():
            got_ready.cancel()
            raise CvStartError(f"live.py exited during start-up (exit code {proc.returncode}); "
                               f"see the 'cv |' lines above")
        exited.cancel()

    async def _stream(self, proc: asyncio.subprocess.Process, session_id: str) -> bool:
        """Feed records until the video ends (True) or the drive ends first (False)."""
        assert proc.stdout is not None
        prev = 0
        times: list[float] = []
        try:
            while True:
                if not self._same_session(session_id):
                    log.info("CV: the drive ended in the app, stopping the video")
                    return False
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=0.5)
                except TimeoutError:
                    continue
                if not raw:
                    return True
                record = parse_record(raw.decode("utf-8", "replace"))
                if record is None or not self._same_session(session_id):
                    continue
                video_t, flags = record
                times.append(video_t)
                prev = await self._feed(video_t, flags, prev)
        finally:
            self._log_timing(times)

    def _log_timing(self, times: list[float]) -> None:
        """How often CV records came: gaps over 2 s make the engine treat the stream as lost."""
        if len(times) < 2:
            log.warning("CV: only %d record(s) this drive", len(times))
            return
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        long_gaps = sum(1 for g in gaps if g > 2.0)
        log.log(logging.WARNING if long_gaps else logging.INFO,
                "CV timing: %d records over %.1f s video, every %.2f s on average, longest gap "
                "%.2f s, %d gap(s) over 2 s%s", len(times), times[-1] - times[0],
                sum(gaps) / len(gaps), max(gaps), long_gaps,
                " (the engine treats those as a lost stream and ends detections)"
                if long_gaps else "")

    async def _feed(self, video_t: float, flags: int, prev: int) -> int:
        masked = flags & 0xF8
        if masked != prev:
            for bit, action in CV_BITS:
                if (masked ^ prev) & bit:
                    log.info("CV: %s %s (video %.1f s)", action,
                             "detected" if masked & bit else "ended", video_t)
            prev = masked
        # Video time is real time only when live.py's player paces the video (--play) or the
        # source is a camera; without the window, analysis slows the video down, so its
        # time falls behind the clock and the arrival time is the better stamp.
        real_time = self.play or self.source.isdigit()
        ts = self._mono_go + video_t if real_time else None
        await self.dispatch(self.core.on_frame(flags, ts))
        return prev

    async def _read_stderr(self, proc: asyncio.subprocess.Process, ready: asyncio.Event) -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line == "READY":
                ready.set()
                continue
            if line:
                # live.py's own EVENT / timing lines and mediapipe/TensorFlow Lite chatter are
                # detail (shown with -v); keep the rest visible.
                level = logging.DEBUG if line.startswith(_CV_NOISE) else logging.INFO
                log.log(level, "cv | %s", line)

    async def _stop_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    def _same_session(self, session_id: str) -> bool:
        return self.core.session is not None and self.core.session.session_id == session_id
