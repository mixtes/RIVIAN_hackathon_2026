"""CVEngine as the driver: BridgeCore.on_frame and the CvSource process lifecycle.

CvSource tests run a fake live.py (same CLI contract: --stream --wait, READY on stderr,
"timestamp,flags" records on stdout) so no models or video are needed.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from simulated_game_engine import protocol as p
from simulated_game_engine.clock import SystemClock
from simulated_game_engine.core import BridgeCore
from simulated_game_engine.cv_source import CvSource, CvStartError, parse_record

from .conftest import ENGINE_VERSION, of_type, run_for, start_msg


def _start(core: BridgeCore, started_at: datetime | None = None) -> str:
    """session.start like the app sends it. started_at defaults to now (a new drive)."""
    msg = start_msg()
    when = started_at or datetime.now(UTC)
    msg["payload"]["started_at"] = when.isoformat().replace("+00:00", "Z")
    assert [m["type"] for m in core.handle(msg)] == [p.SESSION_ACK]
    return msg["payload"]["session_id"]


# ---- BridgeCore.on_frame -------------------------------------------------------------------

def test_on_frame_without_session_is_ignored(core) -> None:
    assert core.on_frame(0x20) == []


def test_on_frame_escalates_and_clears(core, clock) -> None:
    _start(core)
    assert core.on_frame(0x20) == []                       # HANDS_OFF_WHEEL starts, VISUAL at 1 s
    assert core.status().active_actions == ["HANDS_OFF_WHEEL"]
    [visual] = of_type(run_for(core, clock, 1.0), p.ALERT)
    assert (visual["payload"]["type"], visual["payload"]["level"]) == ("HANDS_OFF_WHEEL", "VISUAL")
    clock.advance(0.2)
    assert core.on_frame(0x20) == []                       # unchanged byte: nothing new
    clock.advance(0.2)
    [clear] = core.on_frame(0x00)
    assert clear["type"] == p.ALERT_CLEAR
    assert clear["payload"]["alert_id"] == visual["payload"]["alert_id"]
    assert core.status().active_actions == []


def test_on_frame_feeds_the_score(core, clock) -> None:
    _start(core)
    # GameEngine scores only the most severe of overlapping actions, so use two separate
    # episodes, each longer than its grace period (records every 0.2 s, like CVEngine).
    for flags in (0x20, 0x40):                             # hands off, then eyes off road
        core.on_frame(flags)
        for _ in range(30):                                # 6 s
            clock.advance(0.2)
            core.on_frame(flags)
        core.on_frame(0x00)
        clock.advance(5.0)                                 # gap longer than the flicker merge
    result = core.end_session()[-1]["payload"]
    counts = {m["type"]: m["count"] for m in result["mistakes"] if m["count"]}
    assert counts == {"HANDS_OFF_WHEEL": 1, "EYES_OFF_ROAD": 1}
    assert result["score"] < 100


def test_on_frame_uses_the_frame_time_not_the_arrival_time(core, clock) -> None:
    """Records processed late, in a burst, must still give the real durations."""
    _start(core)
    go = clock.monotonic()
    clock.advance(10.0)                                    # everything arrives 10 s later
    records = [(0.2 * i, 0x40 if 5 <= i < 25 else 0) for i in range(30)]   # off road 1.0-5.0 s
    for video_t, flags in records:
        core.on_frame(flags, go + video_t)
    result = core.end_session()[-1]["payload"]
    [event] = [e for e in result["events"] if e["type"] == "EYES_OFF_ROAD"]
    assert event["duration_s"] == 4.0                      # not squeezed to ~0 s by the burst


def test_on_frame_never_uses_a_future_time(core, clock) -> None:
    _start(core)
    core.on_frame(0x40, clock.monotonic() + 5.0)           # clamped to now
    clock.advance(1.0)
    core.on_frame(0x00, clock.monotonic())
    result = core.end_session()[-1]["payload"]
    assert all(e["duration_s"] <= 1.0 for e in result["events"])


def test_parse_record() -> None:
    assert parse_record("12.346,32\n") == (12.346, 32)
    assert parse_record("12.346,48,2\n") == (12.346, 48)  # live.py now adds the occupant count
    assert parse_record("garbage") is None
    assert parse_record("x,32") is None
    assert parse_record("1,2,3,4") is None


# ---- CvSource with a fake live.py ----------------------------------------------------------

FAKE_LIVE = textwrap.dedent("""
    import sys, time
    args = sys.argv[1:]
    assert "--stream" in args and "--wait" in args, args
    print("device=cpu; loading models and warming up...", file=sys.stderr, flush=True)
    if "--fail" in args:
        sys.exit(3)
    print("READY", file=sys.stderr, flush=True)
    if not sys.stdin.readline():
        sys.exit(0)
    n = 15 if "--short" in args else 60
    for i in range(n):                                      # hands off 0.3-5.0 s (past grace)
        print(f"{i * 0.1:.3f},{32 if 3 <= i < 50 else 0},1", flush=True)  # t,flags,occupants
        time.sleep(0.1)
""")


def _cv_dir(tmp_path: Path) -> Path:
    (tmp_path / "live.py").write_text(FAKE_LIVE)
    (tmp_path / "video.mp4").write_bytes(b"not really a video")
    return tmp_path


class _Sink:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, messages) -> None:
        self.sent.extend(messages)


async def _ticker(core: BridgeCore, sink: _Sink) -> None:
    while True:
        await asyncio.sleep(0.05)
        await sink(core.tick())


async def _wait(cond, timeout: float = 15.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "timed out"
        await asyncio.sleep(0.05)


def _source(core, sink, cv_dir: Path, *extra: str, **kw) -> CvSource:
    return CvSource(core, sink, source=str(cv_dir / "video.mp4"), cv_dir=cv_dir,
                    python=sys.executable, extra_args=list(extra), play=False, **kw)


async def test_video_plays_on_drive_start_alerts_reach_the_app_and_drive_ends(
        engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    sink = _Sink()
    src = _source(core, sink, _cv_dir(tmp_path))
    ticker = asyncio.ensure_future(_ticker(core, sink))
    src.start()
    try:
        await _wait(lambda: src.state == "ready")          # models "loaded", waiting for a drive
        await asyncio.sleep(0.3)
        assert sink.sent == []                             # nothing plays before the drive
        sid = _start(core)
        await _wait(lambda: core.session is None)          # video ended -> drive ended
        alerts = of_type(sink.sent, p.ALERT)
        assert ("HANDS_OFF_WHEEL", "VISUAL") in {(a["payload"]["type"], a["payload"]["level"])
                                                 for a in alerts}
        assert all(a["payload"]["session_id"] == sid for a in alerts)
        assert len(of_type(sink.sent, p.ALERT_CLEAR)) >= 1
        [result] = of_type(sink.sent, p.DRIVE_RESULT)
        assert result["payload"]["extra"]["end_reason"] == p.ENGINE_END_REASON
        counts = {m["type"]: m["count"] for m in result["payload"]["mistakes"] if m["count"]}
        assert counts == {"HANDS_OFF_WHEEL": 1}
        await _wait(lambda: src.state == "ready")          # pre-loaded again for the next drive
    finally:
        ticker.cancel()
        await src.close()


async def test_drive_ended_in_app_stops_the_video(engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    sink = _Sink()
    src = _source(core, sink, _cv_dir(tmp_path))
    src.start()
    try:
        await _wait(lambda: src.state == "ready")
        sid = _start(core)
        await _wait(lambda: src.state == "playing")
        core.handle({"v": 1, "type": p.SESSION_END, "msg_id": "m1", "ts": "2026-11-06T08:00:00Z",
                     "payload": {"session_id": sid, "reason": "MANUAL"}})
        await _wait(lambda: src.state == "ready")           # stopped, then re-loaded
        assert core.session is None
    finally:
        await src.close()


async def test_keep_drive_leaves_the_drive_active(engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    sink = _Sink()
    src = _source(core, sink, _cv_dir(tmp_path), "--short", end_drive_when_done=False)
    src.start()
    try:
        await _wait(lambda: src.state == "ready")
        _start(core)
        await _wait(lambda: src.state == "stopped")         # video done
        await asyncio.sleep(0.3)
        assert core.session is not None
        assert of_type(sink.sent, p.DRIVE_RESULT) == []
    finally:
        await src.close()


async def test_resumed_drive_does_not_play_but_the_next_new_drive_does(engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    sink = _Sink()
    src = _source(core, sink, _cv_dir(tmp_path), "--short")
    src.start()
    try:
        await _wait(lambda: src.state == "ready")
        old = _start(core, started_at=datetime.now(UTC) - timedelta(minutes=5))   # app resumed it
        await asyncio.sleep(1.0)
        assert src.state == "ready"                         # still waiting: no video for it
        assert sink.sent == []
        core.handle({"v": 1, "type": p.SESSION_END, "msg_id": "m1", "ts": "2026-11-07T08:00:00Z",
                     "payload": {"session_id": old, "reason": "MANUAL"}})
        new = _start(core)                                  # driver unlocks: a new drive
        await _wait(lambda: src.state == "playing")
        assert core.session is not None and core.session.session_id == new
    finally:
        await src.close()


async def test_play_resumed_option_plays_a_resumed_drive(engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    src = _source(core, _Sink(), _cv_dir(tmp_path), "--short", play_resumed=True)
    src.start()
    try:
        await _wait(lambda: src.state == "ready")
        _start(core, started_at=datetime.now(UTC) - timedelta(minutes=5))
        await _wait(lambda: src.state == "playing")
    finally:
        await src.close()


def test_is_resumed_uses_the_app_start_time_with_slack(engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    src = _source(core, _Sink(), _cv_dir(tmp_path))
    now = src.started_wall
    _start(core, started_at=now - timedelta(seconds=3))     # clock skew: still a new drive
    assert not src.is_resumed(core.session)
    core.session.app_started_at = now - timedelta(minutes=2)
    assert src.is_resumed(core.session)
    core.session.app_started_at = None                      # app didn't say: treat as new
    assert not src.is_resumed(core.session)


async def test_start_up_failure_stops_the_source(engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    src = _source(core, _Sink(), _cv_dir(tmp_path), "--fail")
    task = src.start()
    await asyncio.wait_for(task, timeout=15)                # ends by itself, no respawn loop
    assert src.state == "stopped"


def test_check_rejects_missing_video_and_old_engine(engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    cv_dir = _cv_dir(tmp_path)
    with pytest.raises(CvStartError, match="video not found"):
        CvSource(core, _Sink(), source=str(tmp_path / "nope.mp4"), cv_dir=cv_dir,
                 python=sys.executable).check()

    class OldSystem:
        pass

    old = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    old.system = OldSystem()
    with pytest.raises(CvStartError, match="on_frame"):
        _source(old, _Sink(), cv_dir).check()


def test_command_has_the_live_py_flags(engine, tmp_path) -> None:
    core = BridgeCore(engine.new_system(), SystemClock(), engine_version=ENGINE_VERSION)
    cmd = CvSource(core, _Sink(), source=str(tmp_path / "v.mp4"), cv_dir=tmp_path,
                   python="py", extra_args=["--stride", "6"]).command()
    assert cmd[:3] == ["py", "-u", str(tmp_path / "live.py")]
    for flag in ("--stream", "--wait", "--play", "--realtime", "--stride"):
        assert flag in cmd
    cam = CvSource(core, _Sink(), source="0", cv_dir=tmp_path, python="py").command()
    assert "--realtime" not in cam and cam[3] == "0"
