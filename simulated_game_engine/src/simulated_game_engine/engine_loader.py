"""Loads the real engine library from `RivianHackatonEngine/` without modifying it.

The engine's modules use top-level imports (`from AlertEngine.AlertEngine import ...`),
so its folder must be on `sys.path`. Its entry module is called `main.py`, which is too
generic to import as `main`, so it is loaded from its file path under a private name.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

ENGINE_PATH_ENV = "SIM_ENGINE_PATH"
_MODULE_NAME = "_rivian_hackathon_engine_main"


class EngineLoadError(RuntimeError):
    pass


def default_engine_path() -> Path:
    """`<repo>/RivianHackatonEngine`, resolved from this file so it works from any cwd.

    Layout: <repo>/simulated_game_engine/src/simulated_game_engine/engine_loader.py
    """
    env = os.environ.get(ENGINE_PATH_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "RivianHackatonEngine"


@dataclass(frozen=True)
class EngineInfo:
    path: Path
    module: ModuleType
    version: str | None  # the engine's `__version__`, if it exposes one
    detected_actions: tuple[str, ...] | None = None  # the engine's DETECTED_ACTIONS, if any

    def new_system(self) -> Any:
        """A fresh `SafetySystem` (AlertEngine + GameEngine)."""
        return self.module.SafetySystem()


def load_engine(path: str | Path | None = None) -> EngineInfo:
    engine_dir = Path(path).expanduser().resolve() if path else default_engine_path()
    main_py = engine_dir / "main.py"
    if not main_py.is_file():
        raise EngineLoadError(
            f"RivianHackatonEngine not found: {main_py} does not exist "
            f"(use --engine-path or ${ENGINE_PATH_ENV})"
        )

    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))

    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None and Path(cached.__file__ or "").resolve() == main_py:
        module = cached
    else:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, main_py)
        if spec is None or spec.loader is None:
            raise EngineLoadError(f"cannot import {main_py}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        # Don't write __pycache__ into the engine team's folder (it's read-only for us).
        previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # pragma: no cover - depends on the engine team's code
            sys.modules.pop(_MODULE_NAME, None)
            raise EngineLoadError(f"importing {main_py} failed: {exc}") from exc
        finally:
            sys.dont_write_bytecode = previous

    if not hasattr(module, "SafetySystem"):
        raise EngineLoadError(f"{main_py} has no SafetySystem class")

    version = getattr(module, "__version__", None)
    detected = getattr(module, "DETECTED_ACTIONS", None)
    return EngineInfo(path=engine_dir, module=module, version=str(version) if version else None,
                      detected_actions=tuple(str(a) for a in detected) if detected else None)
