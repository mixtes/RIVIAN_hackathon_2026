"""
simulate_penalties - try out penalty-config variants against a spread of
simulated driving outcomes, to help balance scores so they feel fair.

A "scenario" is a simulated drive (a list of action events + how long the
drive lasted). A "config variant" is a full set of ActionScoreConfig rules
(how harshly each action is punished). This script runs every scenario
through every config variant and prints the resulting safety scores side
by side, plus a fairness check that flags a config where a worse scenario
somehow scores better than a milder one.

Run it directly:
    python simulate_penalties.py                  # compare all variants
    python simulate_penalties.py current strict    # only compare these

To try your own penalty balance, add an entry to CONFIG_VARIANTS (either a
full {action: ActionScoreConfig} dict, or scale_config(...) applied to an
existing one). To try a new driving outcome, add a Scenario to SCENARIOS.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from GameEngine import ActionScoreConfig, DEFAULT_SCORE_CONFIG, DriveReport, GameEngine, MAX_SCORE


# ---------------------------------------------------------------------------
# Scenarios - simulated driving outcomes
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    description: str
    events: List[Tuple[str, bool, float]]   # (action, is_active, timestamp)
    drive_duration: float
    # Relative severity for the fairness check: higher rank should never
    # score better than a lower rank under a well-balanced config.
    # None = edge case, excluded from that ordering check.
    rank: Optional[int] = None


def _repeated_taps(action: str, start: float, count: int, tap_len: float, gap: float):
    """Helper: `count` short on/off blips of `action`, `gap` seconds apart."""
    events = []
    t = start
    for _ in range(count):
        events.append((action, True, t))
        events.append((action, False, t + tap_len))
        t += gap
    return events


SCENARIOS: List[Scenario] = [
    Scenario(
        name="clean_drive",
        description="No dangerous actions at all.",
        events=[],
        drive_duration=180.0,
        rank=0,
    ),
    Scenario(
        name="quick_glance",
        description="One harmless 0.3s glance away, under the grace period.",
        events=[("EYES_OFF_ROAD", True, 50.0), ("EYES_OFF_ROAD", False, 50.3)],
        drive_duration=180.0,
        rank=0,
    ),
    Scenario(
        name="light_offender",
        description="A short phone glance and a yawn, nothing sustained.",
        events=[
            ("PHONE_USE", True, 20.0), ("PHONE_USE", False, 22.5),
            ("YAWN", True, 95.0), ("YAWN", False, 95.4),
        ],
        drive_duration=180.0,
        rank=1,
    ),
    Scenario(
        name="moderate_offender",
        description="A handful of medium-length distractions across categories.",
        events=[
            ("PHONE_USE", True, 15.0), ("PHONE_USE", False, 19.0),
            ("HANDS_OFF_WHEEL", True, 40.0), ("HANDS_OFF_WHEEL", False, 43.5),
            ("EATING_DRINKING", True, 70.0), ("EATING_DRINKING", False, 76.0),
            ("EYES_OFF_ROAD", True, 120.0), ("EYES_OFF_ROAD", False, 122.0),
        ],
        drive_duration=180.0,
        rank=2,
    ),
    Scenario(
        name="heavy_offender",
        description="Repeated microsleeps and long phone calls - a genuinely unsafe drive.",
        events=[
            ("EYES_CLOSED", True, 30.0), ("EYES_CLOSED", False, 34.0),
            ("PHONE_CALL", True, 50.0), ("PHONE_CALL", False, 65.0),
            ("HANDS_OFF_WHEEL", True, 66.0), ("HANDS_OFF_WHEEL", False, 72.0),
            ("EYES_CLOSED", True, 100.0), ("EYES_CLOSED", False, 103.5),
            ("PHONE_CALL", True, 140.0), ("PHONE_CALL", False, 160.0),
        ],
        drive_duration=180.0,
        rank=3,
    ),
    Scenario(
        name="single_long_call",
        description="Edge case: one 60s phone call, to check the per-occurrence cap.",
        events=[("PHONE_CALL", True, 60.0), ("PHONE_CALL", False, 120.0)],
        drive_duration=180.0,
        rank=None,
    ),
    Scenario(
        name="many_short_taps",
        description="Edge case: ten 0.4s phone taps - death by a thousand cuts.",
        events=_repeated_taps("PHONE_USE", start=10.0, count=10, tap_len=0.4, gap=15.0),
        drive_duration=180.0,
        rank=None,
    ),
]


# ---------------------------------------------------------------------------
# Config variants - different ways to balance the penalties
# ---------------------------------------------------------------------------

def scale_config(base: Dict[str, ActionScoreConfig], *, occurrence_mult: float = 1.0,
                  duration_mult: float = 1.0, grace_delta: float = 0.0,
                  cap_mult: float = 1.0) -> Dict[str, ActionScoreConfig]:
    """Build a new config by scaling every rule in `base`."""
    scaled = {}
    for action, cfg in base.items():
        cap = None if cfg.max_penalty_per_occurrence is None else cfg.max_penalty_per_occurrence * cap_mult
        scaled[action] = ActionScoreConfig(
            penalty_per_occurrence=cfg.penalty_per_occurrence * occurrence_mult,
            penalty_per_second=cfg.penalty_per_second * duration_mult,
            grace_seconds=max(0.0, cfg.grace_seconds + grace_delta),
            max_penalty_per_occurrence=cap,
        )
    return scaled


CONFIG_VARIANTS: Dict[str, Dict[str, ActionScoreConfig]] = {
    "current": dict(DEFAULT_SCORE_CONFIG),
    "lenient": scale_config(DEFAULT_SCORE_CONFIG, occurrence_mult=0.5, duration_mult=0.5,
                             grace_delta=0.5, cap_mult=1.5),
    "strict": scale_config(DEFAULT_SCORE_CONFIG, occurrence_mult=1.5, duration_mult=1.5,
                            grace_delta=-0.25, cap_mult=0.75),
    "duration_focused": scale_config(DEFAULT_SCORE_CONFIG, occurrence_mult=0.0, duration_mult=1.5),
    "frequency_focused": scale_config(DEFAULT_SCORE_CONFIG, occurrence_mult=2.0, duration_mult=0.0),
}


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def run_scenario(config: Dict[str, ActionScoreConfig], scenario: Scenario) -> DriveReport:
    engine = GameEngine(config)
    start = scenario.events[0][2] if scenario.events else 0.0
    engine.start_drive(start)
    for action, is_active, ts in scenario.events:
        engine.process_event(action, is_active, ts)
    return engine.end_drive(scenario.drive_duration)


def simulate(configs: Dict[str, Dict[str, ActionScoreConfig]],
             scenarios: List[Scenario]) -> Dict[str, Dict[str, DriveReport]]:
    return {
        config_name: {s.name: run_scenario(config, s) for s in scenarios}
        for config_name, config in configs.items()
    }


def check_fairness(results: Dict[str, DriveReport], scenarios: List[Scenario]) -> List[str]:
    """For one config's results: flag a milder scenario scoring worse than a harsher one."""
    ranked = sorted((s for s in scenarios if s.rank is not None), key=lambda s: s.rank)
    issues = []
    for prev, cur in zip(ranked, ranked[1:]):
        prev_score = results[prev.name].score
        cur_score = results[cur.name].score
        if cur_score > prev_score + 1e-9:
            issues.append(
                f"'{cur.name}' (rank {cur.rank}, score {cur_score:.1f}) scored higher than "
                f"less severe '{prev.name}' (rank {prev.rank}, score {prev_score:.1f})"
            )
    return issues


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_score_table(results: Dict[str, Dict[str, DriveReport]],
                       scenarios: List[Scenario], config_names: List[str]) -> None:
    name_width = max(len(s.name) for s in scenarios) + 2
    col_width = max(max(len(c) for c in config_names) + 2, 10)

    header = f"{'scenario':<{name_width}}" + "".join(f"{c:>{col_width}}" for c in config_names)
    print(header)
    print("-" * len(header))
    for s in scenarios:
        row = f"{s.name:<{name_width}}"
        for c in config_names:
            score = results[c][s.name].score
            row += f"{score:>{col_width}.1f}"
        print(row)


def print_fairness(results: Dict[str, Dict[str, DriveReport]],
                    scenarios: List[Scenario], config_names: List[str]) -> None:
    print("\nFairness check - severity should stay monotonic:")
    print("clean_drive >= light_offender >= moderate_offender >= heavy_offender:")
    any_issue = False
    for c in config_names:
        issues = check_fairness(results[c], scenarios)
        if not issues:
            print(f"  [{c}] OK")
            continue
        any_issue = True
        print(f"  [{c}] ISSUES:")
        for issue in issues:
            print(f"    - {issue}")
    if not any_issue:
        print("  (all configs preserve severity ordering)")


def main(argv: List[str]) -> None:
    config_names = argv if argv else list(CONFIG_VARIANTS.keys())
    unknown = [c for c in config_names if c not in CONFIG_VARIANTS]
    if unknown:
        known = ", ".join(CONFIG_VARIANTS)
        raise SystemExit(f"Unknown config variant(s): {unknown}. Known variants: {known}")

    configs = {c: CONFIG_VARIANTS[c] for c in config_names}
    results = simulate(configs, SCENARIOS)

    print(f"Max score: {MAX_SCORE:.0f}\n")
    print_score_table(results, SCENARIOS, config_names)
    print_fairness(results, SCENARIOS, config_names)


if __name__ == "__main__":
    main(sys.argv[1:])
