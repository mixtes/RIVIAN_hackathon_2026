"""GameEngine tests: GameEngine/CAR_FRONTEND_INTERFACE.md section 7 + clip_score.py scoring.

Run from RivianHackatonEngine/:  python -m unittest discover tests
"""

from __future__ import annotations

import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from GameEngine.GameEngine import DEFAULT_SCORE_CONFIG, ActionScoreConfig, GameEngine  # noqa: E402

ACTIONS = ("PHONE_CALL", "PHONE_USE", "HANDS_OFF_WHEEL", "EYES_CLOSED",
           "EYES_OFF_ROAD", "YAWN", "EATING_DRINKING", "SMOKING")


def drive(events, end=30.0, engine=None):
    """events: (action, start, end) spans, fed in time order."""
    g = engine or GameEngine()
    g.start_drive(0.0)
    edges = sorted([(s, a, True) for a, s, _ in events] + [(e, a, False) for a, _, e in events],
                   key=lambda x: (x[0], x[2]))
    for t, a, active in edges:
        g.process_event(a, active, t)
    return g, g.end_drive(end)


def check_consistency(tc, g, r):
    for action, summary in r.actions.items():
        penalized = [o for o in r.occurrences if o.action == action and g.score_occurrence(o) > 0]
        tc.assertEqual(summary.count, len(penalized), action)
        tc.assertAlmostEqual(summary.total_duration, sum(o.duration for o in penalized))
        tc.assertAlmostEqual(summary.longest_duration,
                             max((o.duration for o in penalized), default=0.0))
        tc.assertGreaterEqual(summary.penalty, 0.0)
    tc.assertTrue(0.0 <= r.score <= 100.0)


class AcceptanceTests(unittest.TestCase):

    def test_1_csv(self):
        r = GameEngine().run_from_csv(os.path.join(ROOT, "GameEngine", "test_inputs.csv"))
        self.assertEqual(set(r.actions), set(ACTIONS))
        self.assertTrue(0 <= r.score <= 100)

    def test_2_under_grace(self):
        g, r = drive([("HANDS_OFF_WHEEL", 1.0, 2.0)])        # grace 3 s
        [occ] = r.occurrences
        self.assertEqual(r.actions["HANDS_OFF_WHEEL"].count, 0)
        self.assertEqual(g.score_occurrence(occ), 0.0)
        self.assertEqual(r.score, 100.0)

    def test_3_repeated_true(self):
        g = GameEngine()
        g.start_drive(0.0)
        for t in (0.0, 1.0, 2.0):
            g.process_event("PHONE_CALL", True, t)
        g.process_event("PHONE_CALL", False, 5.0)
        [occ] = g.end_drive(10.0).occurrences
        self.assertEqual(occ.duration, 5.0)

    def test_4_open_closed_at_end(self):
        g = GameEngine()
        g.start_drive(0.0)
        g.process_event("PHONE_CALL", True, 2.0)
        [occ] = g.end_drive(10.0).occurrences
        self.assertEqual(occ.end_time, 10.0)

    def test_5_consistency_property(self):
        rng = random.Random(42)
        for _ in range(200):
            g = GameEngine()
            g.start_drive(0.0)
            t = 0.0
            for _ in range(rng.randint(0, 40)):
                t += rng.choice([0.1, 0.3, 0.6, 1.0, 2.5, 5.0])
                g.process_event(rng.choice(ACTIONS), rng.random() < 0.55, t)
            r = g.end_drive(t + rng.random() * 10)
            check_consistency(self, g, r)
            self.assertAlmostEqual(sum(a.penalty for a in r.actions.values()), 100.0 - r.score)

    def test_6_enormous_penalty(self):
        g = GameEngine({"PHONE_CALL": ActionScoreConfig(weight=1e9)})
        g.start_drive(0.0)
        g.process_event("PHONE_CALL", True, 0.0)
        r = g.end_drive(30.0)
        self.assertEqual(r.score, 0.0)

    def test_7_end_without_start(self):
        r = GameEngine().end_drive(5.0)
        self.assertEqual((r.score, r.drive_duration), (100.0, 0.0))

    def test_8_unknown_action(self):
        g, r = drive([("DRIVE_END", 0.0, 5.0)])
        self.assertEqual(r.occurrences, [])
        self.assertEqual(r.score, 100.0)

    def test_9_deterministic(self):
        events = [("PHONE_USE", 1, 6), ("HANDS_OFF_WHEEL", 3, 12), ("EYES_OFF_ROAD", 20, 25)]
        _, r1 = drive(events)
        _, r2 = drive(events)
        self.assertEqual(r1, r2)


class ClipScoreTests(unittest.TestCase):
    """Same numbers as the DEMO in clip_score.py, for a 30 s drive."""

    def score(self, events, end=30.0):
        return round(drive(events, end)[1].score, 1)

    def test_demo_clips(self):
        self.assertEqual(self.score([]), 100.0)
        self.assertEqual(self.score([("EYES_OFF_ROAD", 5, 8)]), 96.2)
        self.assertEqual(self.score([("EYES_OFF_ROAD", 5, 10)]), 89.5)
        self.assertEqual(self.score([("EYES_OFF_ROAD", 2, 6), ("EYES_OFF_ROAD", 12, 16),
                                     ("EYES_OFF_ROAD", 22, 26)]), 80.6)
        self.assertEqual(self.score([("PHONE_CALL", 10, 15)]), 77.2)

    def test_overlap_only_most_severe(self):
        g, r = drive([("PHONE_CALL", 5, 15), ("EYES_OFF_ROAD", 5, 15), ("HANDS_OFF_WHEEL", 6, 14)])
        self.assertEqual(round(r.score, 1), 48.2)
        self.assertEqual(r.actions["PHONE_CALL"].count, 1)
        self.assertEqual(r.actions["EYES_OFF_ROAD"].count, 0)
        self.assertEqual(r.actions["HANDS_OFF_WHEEL"].count, 0)
        self.assertEqual(len(r.occurrences), 3)                # still all recorded
        check_consistency(self, g, r)

    def test_partial_overlap_counts_uncovered_time(self):
        # gaze 0-10, phone 2-4 -> gaze is penalized for 8 uncovered seconds
        g, r = drive([("EYES_OFF_ROAD", 0, 10), ("PHONE_CALL", 2, 4)])
        gaze = [o for o in r.occurrences if o.action == "EYES_OFF_ROAD"][0]
        self.assertAlmostEqual(g.score_occurrence(gaze), 0.6 * (1 + 8 - 2.0) ** 1.5)

    def test_flicker_merged(self):
        _, r = drive([("EYES_OFF_ROAD", 4.0, 6.0), ("EYES_OFF_ROAD", 6.3, 8.0),
                      ("EYES_OFF_ROAD", 8.2, 10.0)])
        self.assertEqual(len(r.occurrences), 1)
        self.assertEqual(r.actions["EYES_OFF_ROAD"].count, 1)
        self.assertEqual(round(r.score, 1), 85.6)

    def test_long_drive_is_scaled(self):
        # The same mistake rate (one 5 s phone use per 30 s) scores the same.
        short = self.score([("PHONE_CALL", 10, 15)], end=30.0)
        long_ = self.score([("PHONE_CALL", 10 + 30 * i, 15 + 30 * i) for i in range(10)], end=300.0)
        self.assertEqual(short, long_)

    def test_all_actions_configured(self):
        self.assertEqual(set(DEFAULT_SCORE_CONFIG), set(ACTIONS))


if __name__ == "__main__":
    unittest.main()
