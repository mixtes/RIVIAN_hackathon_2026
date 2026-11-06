from pathlib import Path

import pytest

from simulated_game_engine.engine_loader import (
    EngineLoadError,
    default_engine_path,
    load_engine,
)


def test_default_path_points_at_repo_engine() -> None:
    path = default_engine_path()
    assert path.name == "RivianHackatonEngine"
    assert (path / "main.py").is_file()


def test_load_engine_gives_working_safety_system(engine) -> None:
    system = engine.new_system()
    system.start_drive(0.0)
    [alert] = system.on_event("phone_call", True, 0.0)      # engine returns a list of updates
    assert alert.level.name == "VISUAL" and alert.alert_id
    assert len(system.alerts.config) == 8
    assert engine.version and engine.detected_actions
    assert set(engine.detected_actions) <= set(system.alerts.config)


def test_load_engine_is_cwd_independent(engine, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_engine().path == engine.path


def test_bad_path_raises(tmp_path: Path) -> None:
    with pytest.raises(EngineLoadError):
        load_engine(tmp_path)
