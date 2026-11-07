from datetime import UTC, datetime

from simulated_game_engine.mapping import map_drive_report, to_wall

from .conftest import ALL_ACTIONS


def test_to_wall_conversion() -> None:
    wall_start = datetime(2026, 11, 6, 8, 0, 0, tzinfo=UTC)
    assert to_wall(1010.5, wall_start=wall_start, mono_start=1000.0) == datetime(
        2026, 11, 6, 8, 0, 10, 500000, tzinfo=UTC)


def _report(engine):
    system = engine.new_system()
    game = system.game
    game.start_drive(1000.0)
    game.process_event("PHONE_CALL", True, 1010.0)
    game.process_event("PHONE_CALL", False, 1018.0)       # 8 s, grace 1: 1.0 * (1+7)^1.5 = 22.63
    game.process_event("EYES_OFF_ROAD", True, 1030.0)
    game.process_event("EYES_OFF_ROAD", False, 1030.3)    # 0.3 s, grace 0: 0.6 * (1+0.3)^1.5 = 0.89
    game.process_event("EYES_OFF_ROAD", True, 1045.0)
    game.process_event("EYES_OFF_ROAD", False, 1048.0)    # 3 s, grace 0: 0.6 * (1+3)^1.5 = 4.80
    # total 28.32, scaled by 30 s / 120 s drive = 7.08 -> score 100 * 2^(-7.08/30) = 84.91
    return game, game.end_drive(1120.0)


def test_drive_report_mapping(engine) -> None:
    game, report = _report(engine)
    wall_start = datetime(2026, 11, 6, 8, 0, 0, tzinfo=UTC)
    result = map_drive_report(
        report, session_id="s1", user_id="u1", wall_start=wall_start, mono_start=1000.0,
        end_mono=1120.0, engine_version="v", end_reason="MANUAL",
        is_penalized=lambda o: game.score_occurrence(o) > 0,
    ).model_dump(mode="json")

    assert result["score"] == 84.91
    assert result["started_at"] == "2026-11-06T08:00:00.000Z"
    assert result["ended_at"] == "2026-11-06T08:02:00.000Z"
    assert result["distance_km"] is None
    mistakes = {m["type"]: m for m in result["mistakes"]}
    assert mistakes["PHONE_CALL"] == {"type": "PHONE_CALL", "count": 1,
                                      "total_duration_s": 8.0, "max_duration_s": 8.0}
    assert mistakes["EYES_OFF_ROAD"]["count"] == 2   # the 0.3 s glance counts too (no grace)
    assert mistakes["EYES_OFF_ROAD"]["total_duration_s"] == 3.3
    # events = penalized occurrences only, in time order, wall-clock
    assert result["events"] == [
        {"type": "PHONE_CALL", "started_at": "2026-11-06T08:00:10.000Z", "duration_s": 8.0},
        {"type": "EYES_OFF_ROAD", "started_at": "2026-11-06T08:00:30.000Z", "duration_s": 0.3},
        {"type": "EYES_OFF_ROAD", "started_at": "2026-11-06T08:00:45.000Z", "duration_s": 3.0},
    ]
    assert {k: v for k, v in result["extra"]["penalties"].items() if v} == {
        "EYES_OFF_ROAD": 3.03, "PHONE_CALL": 12.06}     # points lost, sum = 100 - score
    assert result["extra"]["end_reason"] == "MANUAL"


def test_clean_drive_lists_configured_types_with_zero(engine) -> None:
    game = engine.new_system().game
    game.start_drive(0.0)
    report = game.end_drive(600.0)
    result = map_drive_report(
        report, session_id="s", user_id="u", wall_start=datetime(2026, 1, 1, tzinfo=UTC),
        mono_start=0.0, end_mono=600.0, engine_version="v", end_reason="ENGINE",
        is_penalized=lambda o: True,
    )
    assert result.score == 100.0
    assert result.events == []
    assert {m.type for m in result.mistakes} == set(ALL_ACTIONS)
    assert all(m.count == 0 for m in result.mistakes)
