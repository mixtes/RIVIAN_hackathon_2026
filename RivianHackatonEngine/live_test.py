"""
Live end-to-end test: CVEngine -> SafetySystem (AlertEngine + GameEngine).

Plays the video in a window (CVEngine/live.py --show --realtime, with its own
overlays: wheel, hands, head, flags byte) and prints in this terminal:
  - what CVEngine detects (bit changes of the byte)       cv     HANDS_OFF_WHEEL on
  - what the AlertEngine outputs (alerts and clears)       ALERT  HANDS_OFF_WHEEL VISUAL: ...
  - the GameEngine's DriveReport when the video ends (or you press q / Ctrl+C)

Usage (from RivianHackatonEngine/, with the venv that has CVEngine/requirements.txt):
    python live_test.py CVEngine/videos/drive.mp4
    python live_test.py CVEngine/videos/drive.mp4 --stride 6 --method pose --head pose   # faster on CPU
    python live_test.py 0                                                                # webcam

Options are passed through to live.py (--stride, --method, --head, ...).
--quiet-cv hides live.py's own log lines (calibration, EVENT lines, timing).
Press q in the video window, or Ctrl+C here, to stop early.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CV_DIR = os.path.join(HERE, "CVEngine")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from main import CV_FLAG_ACTIONS, Alert, AlertUpdate, SafetySystem, read_cv_records  # noqa: E402

TICK = 0.1  # seconds of video time between escalation checks, like the bridge

if os.name == "nt":
    os.system("")  # turn on ANSI colors in the Windows console
RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
LEVEL_COLOR = {"VISUAL": "\033[33m", "AUDIO": "\033[93m", "HAPTIC": "\033[91m",
               "CRITICAL": "\033[97;41m"}
GREEN, CYAN = "\033[32m", "\033[36m"


def say(text: str) -> None:
    print(text + RESET, flush=True)


def show_updates(t: float, updates: List[AlertUpdate]) -> None:
    for u in updates:
        if isinstance(u, Alert):
            ack = "  [needs dismiss]" if u.requires_ack else ""
            say(f"[{t:7.2f}s] {LEVEL_COLOR[u.level.name]}{BOLD}ALERT  {u.action} {u.level.name}"
                f"{RESET}{LEVEL_COLOR[u.level.name]}: {u.message}{ack}")
        else:
            say(f"[{t:7.2f}s] {GREEN}CLEAR  {u.action} ({u.reason}, was {u.last_level.name}, "
                f"{u.duration:.1f}s)")


def show_flag_changes(t: float, before: int, after: int) -> None:
    for bit, action in CV_FLAG_ACTIONS:
        if (before ^ after) & bit:
            state = "on " if after & bit else "off"
            say(f"[{t:7.2f}s] {CYAN}cv     {action} {state}{RESET}{DIM}   (flags 0x{after:02X})")


def forward_stderr(stream, quiet: bool) -> None:
    for line in stream:
        if not quiet:
            say(f"{DIM}  live.py | {line.rstrip()}")


def check_dependencies(extra: List[str]) -> Optional[str]:
    def opt(name: str, default: str) -> str:
        return extra[extra.index(name) + 1] if name in extra[:-1] else default

    needs_mediapipe = opt("--method", "hybrid") != "pose" or opt("--head", "face") == "face"
    missing = [m for m in ("cv2", "ultralytics") if importlib.util.find_spec(m) is None]
    if needs_mediapipe and importlib.util.find_spec("mediapipe") is None:
        missing.append("mediapipe")
    if not missing:
        return None
    hint = ""
    if missing == ["mediapipe"]:
        hint = "\n  or run without it:  add  --method pose --head pose"
    return (f"Missing Python packages in this interpreter ({sys.executable}): {', '.join(missing)}\n"
            f"  install:  python -m pip install -r CVEngine/requirements.txt{hint}")


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    quiet = "--quiet-cv" in args
    args = [a for a in args if a != "--quiet-cv"]
    source, extra = args[0], args[1:]
    if not source.isdigit():
        source = os.path.abspath(source)
        if not os.path.isfile(source):
            print(f"Video not found: {source}", file=sys.stderr)
            return 2
    problem = check_dependencies(extra)
    if problem:
        print(problem, file=sys.stderr)
        return 2

    cmd = [sys.executable, "-u", os.path.join(CV_DIR, "live.py"), source,
           "--stream", "--show", *([] if source.isdigit() else ["--realtime"]), *extra]
    say(f"{BOLD}Starting CVEngine{RESET}: loading models and calibrating the wheel takes a moment; "
        f"the video window opens after that. Press q in the window or Ctrl+C here to stop.")
    proc = subprocess.Popen(cmd, cwd=CV_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    threading.Thread(target=forward_stderr, args=(proc.stderr, quiet), daemon=True).start()

    system = SafetySystem()
    started = False
    first = last = 0.0
    n = 1                    # next tick at first + n * TICK (no float drift)
    prev_flags = 0
    try:
        for ts, flags in read_cv_records(proc.stdout):
            if not started:
                system.start_drive(ts)
                started, first, last = True, ts, ts
                say(f"[{ts:7.2f}s] {BOLD}drive started{RESET} (first CV record)")
            while round(first + n * TICK, 6) <= ts:
                t = round(first + n * TICK, 6)
                show_updates(t, system.tick_all(t))
                n += 1
            masked = flags & 0xF0
            if masked != prev_flags:
                show_flag_changes(ts, prev_flags, masked)
            show_updates(ts, system.on_frame(ts, flags))
            prev_flags, last = masked, max(last, ts)
    except KeyboardInterrupt:
        say(f"\n{BOLD}stopped{RESET}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    if not started:
        say(f"{BOLD}No CV records arrived{RESET} (live.py exit code {proc.returncode}). "
            f"Run again without --quiet-cv to see why.")
        return 1
    report = system.end_drive(last)
    say(f"\n{BOLD}=== GameEngine report ==={RESET}")
    say(str(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
