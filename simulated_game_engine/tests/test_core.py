from __future__ import annotations

import itertools
import json
import uuid

import pytest

from simulated_game_engine import protocol as p
from simulated_game_engine.core import BridgeCore, NoActiveSessionError

from .conftest import ENGINE_VERSION, inbound, of_type, run_for, start_msg


def _start(core: BridgeCore, sid: str | None = None) -> str:
    msg = start_msg(sid)
    out = core.handle(msg)
    assert [m["type"] for m in out] == [p.SESSION_ACK]
    return msg["payload"]["session_id"]


# ---- envelope / hello --------------------------------------------------------------------

def test_hello_reply(core) -> None:
    [reply] = core.handle(inbound(p.HELLO, app_version="1.0.0", car_id="c", protocol=1))
    assert reply["v"] == 1 and reply["type"] == p.HELLO
    assert reply["ts"].endswith("Z")
    uuid.UUID(reply["msg_id"])
    assert reply["payload"] == {"engine_version": ENGINE_VERSION, "active_session_id": None,
                                "supported_actions": ["EYES_OFF_ROAD", "PHONE_CALL"]}


def test_hello_reports_active_session(core) -> None:
    sid = _start(core)
    [reply] = core.handle(inbound(p.HELLO, protocol=1))
    assert reply["payload"]["active_session_id"] == sid


@pytest.mark.parametrize("msg", [
    {"v": 2, "type": p.HELLO, "msg_id": "m1", "payload": {}},
    {"v": 1, "type": p.HELLO, "msg_id": "m1", "payload": {"protocol": 2}},
])
def test_unsupported_version(core, msg) -> None:
    [err] = core.handle(msg)
    assert err["type"] == p.ERROR
    assert err["payload"]["code"] == p.UNSUPPORTED_VERSION
    assert err["payload"]["ref_msg_id"] == "m1"


def test_bad_json_and_bad_payload_and_unknown_type(core) -> None:
    [err] = core.handle_raw("{not json")
    assert err["payload"]["code"] == p.BAD_REQUEST
    [err] = core.handle(inbound(p.SESSION_START, session_id="not-a-uuid"))
    assert err["payload"]["code"] == p.BAD_REQUEST
    [err] = core.handle(inbound("weird.type"))
    assert err["payload"]["code"] == p.UNKNOWN_TYPE


def test_unknown_fields_are_ignored(core) -> None:
    msg = start_msg()
    msg["extra_field"] = 1
    msg["payload"]["future_field"] = {"x": 1}
    assert [m["type"] for m in core.handle(msg)] == [p.SESSION_ACK]


def test_messages_are_json_serializable(core, clock) -> None:
    _start(core)
    out = core.set_action("phone_call", True)
    out += run_for(core, clock, 3.5)
    out += core.set_action("phone_call", False)
    out += core.end_session()
    for m in out:
        json.dumps(m)


# ---- session lifecycle -------------------------------------------------------------------

def test_session_start_is_idempotent_and_resumes(core, clock) -> None:
    sid = _start(core)
    core.set_action("eyes_off_road", True)
    run_for(core, clock, 1.2)                       # AUDIO alert issued
    clock.advance(0.1)
    out = core.handle(start_msg(sid))               # reconnect: same session_id
    assert [m["type"] for m in out] == [p.SESSION_ACK, p.ALERT]   # alert re-sent
    assert core.session is not None and core.session.session_id == sid
    assert core.active_actions                      # drive was not restarted
    # escalation continues from the original start, not from the resume
    out = run_for(core, clock, 1.4)
    assert [m["payload"]["level"] for m in of_type(out, p.ALERT)] == ["HAPTIC"]


def test_new_session_while_active_ends_old_one_with_driver_switch(core) -> None:
    old = _start(core)
    new_msg = start_msg()
    out = core.handle(new_msg)
    assert [m["type"] for m in out] == [p.DRIVE_RESULT, p.SESSION_ACK]
    assert out[0]["payload"]["session_id"] == old
    assert out[0]["payload"]["extra"]["end_reason"] == "DRIVER_SWITCH"
    assert core.session.session_id == new_msg["payload"]["session_id"]


def test_session_end_produces_result_kept_until_ack(core, clock) -> None:
    sid = _start(core)
    clock.advance(60)
    out = core.handle(inbound(p.SESSION_END, session_id=sid, reason="MANUAL"))
    [result] = of_type(out, p.DRIVE_RESULT)
    assert result["payload"]["session_id"] == sid
    assert result["payload"]["score"] == 100.0
    assert core.session is None
    assert list(core.pending_results) == [sid]

    # re-delivered on every hello until acked (§8.4 rule 3)
    out = core.handle(inbound(p.HELLO, protocol=1))
    assert [m["type"] for m in out] == [p.HELLO, p.DRIVE_RESULT]
    assert out[1]["payload"]["session_id"] == sid
    # repeated session.end for the same session re-sends the same result
    assert core.handle(inbound(p.SESSION_END, session_id=sid))[0]["msg_id"] == result["msg_id"]

    assert core.handle(inbound(p.DRIVE_RESULT_ACK, session_id=sid)) == []
    assert not core.pending_results
    assert [m["type"] for m in core.handle(inbound(p.HELLO, protocol=1))] == [p.HELLO]
    assert core.handle(inbound(p.DRIVE_RESULT_ACK, session_id=sid)) == []  # duplicate ack ok


def test_session_start_for_ended_session_is_rejected(core) -> None:
    sid = _start(core)
    core.end_session()
    out = core.handle(start_msg(sid))
    assert [m["type"] for m in out] == [p.ERROR, p.DRIVE_RESULT]
    assert out[0]["payload"]["code"] == p.SESSION_ENDED
    assert core.session is None
    core.handle(inbound(p.DRIVE_RESULT_ACK, session_id=sid))
    out = core.handle(start_msg(sid))               # still rejected after the ack
    assert [m["type"] for m in out] == [p.ERROR]


def test_session_end_for_unknown_session(core) -> None:
    [err] = core.handle(inbound(p.SESSION_END, session_id=str(uuid.uuid4())))
    assert err["payload"]["code"] == p.NO_ACTIVE_SESSION


def test_driver_events_require_a_session(core) -> None:
    with pytest.raises(NoActiveSessionError):
        core.set_action("phone_call", True)
    with pytest.raises(NoActiveSessionError):
        core.end_session()
    assert core.tick() == []


# ---- alerts ------------------------------------------------------------------------------

def test_phone_call_escalation_reuses_alert_id_and_clears(core, clock) -> None:
    sid = _start(core)
    clock.advance(10)
    [visual] = core.set_action("PHONE_CALL", True)          # upper-case input accepted
    assert visual["type"] == p.ALERT
    pv = visual["payload"]
    assert (pv["type"], pv["level"], pv["session_id"]) == ("PHONE_CALL", "VISUAL", sid)
    assert pv["message"] == "Please put the phone down."
    assert pv["started_at"] == "2026-11-06T08:00:10.000Z"
    assert pv["ttl_s"] == 8

    out = run_for(core, clock, 4.0)
    [audio] = of_type(out, p.ALERT)
    assert audio["payload"]["level"] == "AUDIO"
    assert audio["payload"]["alert_id"] == pv["alert_id"]
    assert 3.0 <= audio["payload"]["duration_so_far_s"] <= 3.15
    assert audio["payload"]["started_at"] == pv["started_at"]

    [clear] = core.set_action("phone_call", False)
    assert clear["type"] == p.ALERT_CLEAR
    assert clear["payload"] == {"alert_id": pv["alert_id"]}

    # next occurrence gets a new id
    [again] = core.set_action("phone_call", True)
    assert again["payload"]["alert_id"] != pv["alert_id"]


def test_eyes_off_road_full_escalation(core, clock) -> None:
    _start(core)
    assert core.set_action("eyes_off_road", True) == []     # first step is at 1 s
    out = of_type(run_for(core, clock, 5.0), p.ALERT)
    assert [m["payload"]["level"] for m in out] == ["AUDIO", "HAPTIC", "CRITICAL"]
    assert len({m["payload"]["alert_id"] for m in out}) == 1
    for msg, threshold in zip(out, (1.0, 2.5, 4.0), strict=True):
        assert threshold <= msg["payload"]["duration_so_far_s"] <= threshold + 0.15
    assert out[-1]["payload"]["ttl_s"] == 15


def test_still_active_alert_is_refreshed_so_the_app_does_not_expire_it(core, clock) -> None:
    _start(core)
    core.set_action("eyes_off_road", True)
    out = of_type(run_for(core, clock, 30.0), p.ALERT)
    levels = [m["payload"]["level"] for m in out]
    assert levels[:3] == ["AUDIO", "HAPTIC", "CRITICAL"]
    refreshes = out[3:]
    # CRITICAL at 4 s, then a keep-alive every 5 s: 9, 14, 19, 24, 29 s
    assert len(refreshes) == 5
    assert {m["payload"]["level"] for m in refreshes} == {"CRITICAL"}
    assert {m["payload"]["alert_id"] for m in out} == {out[0]["payload"]["alert_id"]}
    assert all(m["payload"]["started_at"] == out[0]["payload"]["started_at"] for m in out)
    gaps = [b["payload"]["duration_so_far_s"] - a["payload"]["duration_so_far_s"]
            for a, b in itertools.pairwise(out[2:])]
    assert all(4.95 <= g <= 5.15 for g in gaps)  # within one 0.1 s tick
    assert max(gaps) < p.DEFAULT_TTL_S["VISUAL"]  # always inside the shortest app ttl

    [clear] = core.set_action("eyes_off_road", False)
    assert clear["type"] == p.ALERT_CLEAR
    assert run_for(core, clock, 10.0) == []      # no refresh after the action ended


def test_action_ending_before_first_alert_sends_no_clear(core, clock) -> None:
    _start(core)
    core.set_action("eyes_off_road", True)
    run_for(core, clock, 0.5)
    assert core.set_action("eyes_off_road", False) == []


def test_unconfigured_action_is_forwarded_and_ignored(core, clock) -> None:
    _start(core)
    assert core.set_action("smoking", True) == []
    assert run_for(core, clock, 2.0) == []
    assert core.set_action("smoking", False) == []
    result = core.end_session()[-1]["payload"]
    assert "SMOKING" not in {m["type"] for m in result["mistakes"]}


def test_simultaneous_escalations_all_delivered_in_per_action_mode(core, clock) -> None:
    _start(core)
    core.set_action("phone_call", True)
    core.set_action("eyes_off_road", True)
    out = of_type(run_for(core, clock, 3.5), p.ALERT)
    levels = [(m["payload"]["type"], m["payload"]["level"]) for m in out]
    assert ("EYES_OFF_ROAD", "AUDIO") in levels
    assert ("EYES_OFF_ROAD", "HAPTIC") in levels
    assert ("PHONE_CALL", "AUDIO") in levels


def test_engine_tick_mode_loses_simultaneous_escalations(engine, clock) -> None:
    """Documents the engine limitation that `per-action` mode works around."""
    core = BridgeCore(engine.new_system(), clock, engine_version="v", tick_mode="engine")
    _start(core)
    core.set_action("phone_call", True)
    core.set_action("eyes_off_road", True)
    clock.advance(3.0)                       # phone AUDIO (3 s) and eyes HAPTIC (2.5 s) due
    out = core.tick()
    assert [(m["payload"]["type"], m["payload"]["level"]) for m in out] == [
        ("EYES_OFF_ROAD", "HAPTIC")]


def test_end_session_clears_alerts_then_sends_result(core, clock) -> None:
    sid = _start(core)
    [alert] = core.set_action("phone_call", True)
    clock.advance(8)
    out = core.end_session()
    assert [m["type"] for m in out] == [p.ALERT_CLEAR, p.DRIVE_RESULT]
    assert out[0]["payload"]["alert_id"] == alert["payload"]["alert_id"]
    result = out[1]["payload"]
    assert result["session_id"] == sid
    assert result["user_id"]
    assert result["engine_version"] == ENGINE_VERSION
    assert result["extra"]["end_reason"] == "ENGINE"
    # still-active action is closed at end of drive: 8 s phone call = 9 pts
    assert result["score"] == 91.0
    assert result["events"][0]["duration_s"] == 8.0
    assert not core.active_actions and core.status().alert_ids == {}



def test_engine_version_is_clamped_to_backend_limit(engine, clock) -> None:  # type: ignore[no-untyped-def]
    from simulated_game_engine.app import default_engine_version
    from simulated_game_engine.core import MAX_ENGINE_VERSION_LEN

    assert len(default_engine_version(None)) <= MAX_ENGINE_VERSION_LEN
    core = BridgeCore(engine.new_system(), clock, engine_version="v" * 60)
    assert core.engine_version == "v" * MAX_ENGINE_VERSION_LEN
