"""CVEngine -> SafetySystem.on_frame acceptance tests.

AlertSideTests = AlertEngine/CV_ENGINE_INTERFACE.md section 7.
GameSideTests  = GameEngine/CV_ENGINE_INTERFACE.md section 7.
GapRuleTests   = section 8.1 (gap rule, adopted with CV_GAP_SECONDS = 2.0).

Run from RivianHackatonEngine/:  python -m unittest discover tests
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import main  # noqa: E402
from main import Alert, AlertClear, SafetySystem  # noqa: E402


def _system(**kw):
    """A SafetySystem after start_drive(0.0) that records every on_event call."""
    s = SafetySystem(**kw)
    s.calls = []
    original = s.on_event

    def spy(action, is_active, timestamp=None):
        s.calls.append((action, is_active, timestamp))
        return original(action, is_active, timestamp)

    s.on_event = spy
    s.start_drive(0.0)
    return s


def _hold(s, t0, t1, flags, dt=0.2):
    """Send `flags` every dt seconds from t0 to t1 (like CVEngine's live stream)."""
    t = t0
    while t < t1 - 1e-9:
        s.on_frame(round(t, 3), flags)
        t += dt


def _occ(report, action):
    return [o for o in report.occurrences if o.action == action]


class AlertSideTests(unittest.TestCase):

    def test_1_bit_start_reaches_both_engines(self):
        s = _system()
        s.on_frame(0.0, 0x20)
        self.assertEqual(s.calls, [("HANDS_OFF_WHEEL", True, 0.0)])
        self.assertEqual(s.alerts.active_actions(), ["HANDS_OFF_WHEEL"])
        [occ] = _occ(s.end_drive(5.0), "HANDS_OFF_WHEEL")
        self.assertEqual(occ.start_time, 0.0)

    def test_2_unchanged_bit_no_call_same_alert_id(self):
        s = _system()
        s.on_frame(0.0, 0x20)
        [visual] = s.tick_all(1.0)
        self.assertEqual(s.on_frame(1.2, 0x20), [])
        self.assertEqual(s.on_frame(1.4, 0x20), [])
        self.assertEqual(len(s.calls), 1)
        [audio] = s.tick_all(3.0)
        self.assertEqual(audio.alert_id, visual.alert_id)
        self.assertEqual(len(_occ(s.end_drive(4.0), "HANDS_OFF_WHEEL")), 1)

    def test_3_bit_end_is_one_false_call(self):
        s = _system()
        s.on_frame(0.0, 0x20)
        s.on_frame(0.2, 0x20)
        s.on_frame(0.4, 0x20)
        s.calls.clear()
        s.on_frame(1.0, 0x00)
        self.assertEqual(s.calls, [("HANDS_OFF_WHEEL", False, 1.0)])

    def test_4_two_bits_start_phone_is_phone_use(self):
        s = _system()
        s.on_frame(0.0, 0x30)
        self.assertEqual(s.calls, [("HANDS_OFF_WHEEL", True, 0.0), ("PHONE_USE", True, 0.0)])
        self.assertNotIn("PHONE_CALL", [c[0] for c in s.calls])

    def test_5_end_before_start_in_one_record(self):
        s = _system()
        s.on_frame(0.0, 0x20)
        s.tick_all(1.0)                       # HANDS_OFF_WHEEL VISUAL, so the end clears
        s.calls.clear()
        updates = s.on_frame(2.0, 0x10)
        self.assertEqual(s.calls, [("HANDS_OFF_WHEEL", False, 2.0), ("PHONE_USE", True, 2.0)])
        self.assertIsInstance(updates[0], AlertClear)
        self.assertEqual(updates[0].action, "HANDS_OFF_WHEEL")
        self.assertIsInstance(updates[1], Alert)
        self.assertEqual(updates[1].action, "PHONE_USE")

    def test_6_reserved_bits_ignored(self):
        s = _system()
        self.assertEqual(s.on_frame(0.0, 0x0F), [])
        self.assertEqual(s.calls, [])
        s.on_frame(0.1, 0x2F)
        self.assertEqual(s.calls, [("HANDS_OFF_WHEEL", True, 0.1)])

    def test_7_invalid_records_ignored(self):
        s = _system()
        for ts, flags in [(0.0, 256), (0.0, -1), (0.0, "x"), (0.0, None), (0.0, True),
                          (float("nan"), 0x20), (float("inf"), 0x20), ("0", 0x20), (0.0, 32.0)]:
            self.assertEqual(s.on_frame(ts, flags), [], (ts, flags))
        self.assertEqual(s.calls, [])
        self.assertEqual(s.alerts.active_actions(), [])
        self.assertEqual(s._prev_flags, 0)
        self.assertIsNone(s._prev_ts)

    def test_8_backwards_timestamp_ignored(self):
        s = _system()
        s.on_frame(5.0, 0x20)
        self.assertEqual(s.on_frame(4.0, 0x00), [])
        self.assertEqual(s.alerts.active_actions(), ["HANDS_OFF_WHEEL"])

    def test_9_eyes_closed_latched(self):
        s = _system()
        s.on_frame(0.0, 0x80)
        [a] = s.tick_all(0.5)
        self.assertTrue(a.requires_ack)
        self.assertEqual(s.on_frame(1.0, 0x00), [])           # latched: no clear on end
        self.assertEqual([x.alert_id for x in s.alerts.active_alerts()], [a.alert_id])
        [c] = s.dismiss(a.alert_id, 2.0)
        self.assertEqual(c.reason, "dismissed")

    def test_10_start_and_end_drive_reset_previous_flags(self):
        s = _system()
        s.on_frame(0.0, 0x20)
        s.end_drive(1.0)
        self.assertEqual(s._prev_flags, 0)
        s.calls.clear()
        s.on_frame(2.0, 0x20)                 # fresh occurrence, no stale state
        self.assertEqual(s.calls, [("HANDS_OFF_WHEEL", True, 2.0)])
        s.start_drive(3.0)
        self.assertEqual(s._prev_flags, 0)
        s.calls.clear()
        s.on_frame(3.0, 0x20)
        self.assertEqual(s.calls, [("HANDS_OFF_WHEEL", True, 3.0)])

    def test_11_deterministic(self):
        records = [(0.0, 0x20), (0.5, 0x60), (1.5, 0x40), (3.0, 0x00), (3.2, 0x90), (4.0, 0x00)]

        def run():
            s = SafetySystem()
            s.start_drive(0.0)
            out = []
            for ts, f in records:
                out += s.on_frame(ts, f)
                out += s.tick_all(ts)
            return ([(type(u).__name__, u.action, getattr(u, "level", None), u.timestamp)
                     for u in out], str(s.end_drive(5.0)))

        self.assertEqual(run(), run())

    def test_12_import_main_isolated(self):
        code = ("import main; s = main.SafetySystem(); s.start_drive(0.0); "
                "s.on_frame(0.0, 0x20); print(s.end_drive(1.0).score)")
        r = subprocess.run([sys.executable, "-I", "-S", "-c",
                            f"import sys; sys.path.insert(0, {ROOT!r}); {code}"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class GameSideTests(unittest.TestCase):

    def test_1_one_occurrence_from_bit_edges(self):
        s = _system()
        t = 1.0
        while t <= 4.0 + 1e-9:
            s.on_frame(round(t, 1), 0x20)
            t += 0.2
        s.on_frame(4.2, 0x00)
        r = s.end_drive(10.0)
        [occ] = _occ(r, "HANDS_OFF_WHEEL")
        self.assertEqual((occ.start_time, occ.end_time), (1.0, 4.2))
        self.assertAlmostEqual(occ.duration, 3.2)
        self.assertEqual(r.actions["HANDS_OFF_WHEEL"].count, 1)

    def test_2_short_occurrence_recorded_but_free(self):
        grace = SafetySystem().game.config["HANDS_OFF_WHEEL"].grace_seconds
        s = _system()
        s.on_frame(1.0, 0x20)
        s.on_frame(1.0 + grace / 2, 0x00)     # shorter than the grace period
        r = s.end_drive(10.0)
        [occ] = _occ(r, "HANDS_OFF_WHEEL")
        self.assertEqual(r.actions["HANDS_OFF_WHEEL"].count, 0)
        self.assertEqual(s.game.score_occurrence(occ), 0.0)
        self.assertEqual(r.score, 100.0)

    def test_3_simultaneous_bits_scored_independently(self):
        s = _system()
        _hold(s, 0.0, 5.0, 0x30)              # records every 0.2 s, so no gap
        s.on_frame(5.0, 0x00)
        r = s.end_drive(10.0)
        for action in ("HANDS_OFF_WHEEL", "PHONE_USE"):
            [occ] = _occ(r, action)
            self.assertEqual((occ.start_time, occ.end_time), (0.0, 5.0))
        self.assertEqual(r.actions["PHONE_CALL"].count, 0)
        expected = sum(s.game.score_occurrence(o) for o in r.occurrences)
        self.assertAlmostEqual(r.score, 100.0 - expected)
        self.assertGreater(r.actions["HANDS_OFF_WHEEL"].penalty, 0)
        self.assertGreater(r.actions["PHONE_USE"].penalty, 0)

    def test_4_invalid_and_reserved(self):
        a, b = _system(), _system()
        for ts, flags in [(0.0, 0x0F), (0.1, 256), (0.1, -1), (0.1, "x"), (0.1, None),
                          (0.1, True), (float("nan"), 0x20)]:
            a.on_frame(ts, flags)
        self.assertEqual(a.end_drive(1.0).occurrences, [])
        a, b = _system(), _system()
        a.on_frame(1.0, 0x2F)
        a.on_frame(4.0, 0x00)
        b.on_frame(1.0, 0x20)
        b.on_frame(4.0, 0x00)
        self.assertEqual(str(a.end_drive(10.0)), str(b.end_drive(10.0)))

    def test_5_backwards_timestamp_keeps_occurrence_open(self):
        s = _system()
        s.on_frame(5.0, 0x20)
        s.on_frame(4.0, 0x00)
        [occ] = _occ(s.end_drive(10.0), "HANDS_OFF_WHEEL")
        self.assertEqual((occ.start_time, occ.end_time), (5.0, 10.0))

    def test_6_still_set_at_end_closed_at_end_drive(self):
        s = _system()
        s.on_frame(2.0, 0x20)
        [occ] = _occ(s.end_drive(10.0), "HANDS_OFF_WHEEL")
        self.assertEqual(occ.end_time, 10.0)
        self.assertAlmostEqual(occ.duration, 8.0)

    def test_8_eyes_closed_scored_by_bit_duration(self):
        s = _system()
        _hold(s, 0.0, 3.0, 0x80)
        s.on_frame(3.0, 0x00)
        [occ] = _occ(s.end_drive(10.0), "EYES_CLOSED")
        self.assertAlmostEqual(occ.duration, 3.0)

    def test_9_deterministic_and_bounded(self):
        records = [(t / 10, 0x70 if (t // 7) % 2 else 0x00) for t in range(0, 600)]

        def run():
            s = SafetySystem()
            s.start_drive(0.0)
            for ts, f in records:
                s.on_frame(ts, f)
            return s.end_drive(60.0)

        r1, r2 = run(), run()
        self.assertEqual(str(r1), str(r2))
        self.assertEqual(r1.occurrences, r2.occurrences)
        self.assertTrue(0 <= r1.score <= 100)

    def test_10_end_drive_without_start_drive(self):
        s = SafetySystem()
        s.on_frame(1.0, 0x20)
        s.on_frame(3.0, 0x00)
        r = s.end_drive(5.0)
        self.assertTrue(0 <= r.score <= 100)
        self.assertEqual(len(_occ(r, "HANDS_OFF_WHEEL")), 1)


class GapRuleTests(unittest.TestCase):
    """Section 8.1 / GameEngine test 7, with CV_GAP_SECONDS = 2.0."""

    def test_7_gap_closes_at_previous_record_and_restarts(self):
        s = _system()
        s.on_frame(2.0, 0x20)
        s.on_frame(30.0, 0x20)
        r = s.end_drive(31.0)
        occs = _occ(r, "HANDS_OFF_WHEEL")
        self.assertEqual([(o.start_time, o.end_time) for o in occs], [(2.0, 2.0), (30.0, 31.0)])

    def test_gap_clears_alert_at_previous_timestamp(self):
        s = _system()
        s.on_frame(0.0, 0x20)
        s.on_frame(1.5, 0x20)
        s.tick_all(1.5)                        # VISUAL raised
        updates = s.on_frame(10.0, 0x00)
        [c] = updates
        self.assertIsInstance(c, AlertClear)
        self.assertEqual(c.timestamp, 1.5)
        self.assertEqual(s.alerts.active_actions(), [])

    def test_small_spacing_is_not_a_gap(self):
        s = _system()
        for i in range(20):                    # one record per 1.9 s
            s.on_frame(i * 1.9, 0x20)
        self.assertEqual(len(_occ(s.end_drive(40.0), "HANDS_OFF_WHEEL")), 1)

    def test_gap_rule_can_be_turned_off(self):
        s = _system(gap_seconds=None)
        s.on_frame(2.0, 0x20)
        s.on_frame(30.0, 0x20)
        [occ] = _occ(s.end_drive(31.0), "HANDS_OFF_WHEEL")
        self.assertEqual((occ.start_time, occ.end_time), (2.0, 31.0))


class ReplayTests(unittest.TestCase):

    def test_detected_actions_match_cv_bits(self):
        bit_actions = {a for _, a in main.CV_FLAG_ACTIONS}
        self.assertTrue(set(main.DETECTED_ACTIONS) <= bit_actions)
        self.assertEqual(set(main.DETECTED_ACTIONS), {"EYES_OFF_ROAD", "HANDS_OFF_WHEEL"})

    def test_read_both_live_py_formats(self):
        stream = ["0.000,0", "0.200,32", "", "garbage", "0.400,96"]
        csv = ["frame,time_s,flags,hands_off,eyes_off_road", "0,0.000,0,0,0",
               "6,0.200,32,1,0", "12,0.400,96,1,1"]
        expected = [(0.0, 0), (0.2, 32), (0.4, 96)]
        self.assertEqual(list(main.read_cv_records(stream)), expected)
        self.assertEqual(list(main.read_cv_records(csv)), expected)

    def test_replay_prints_alerts_and_scores(self):
        lines = [f"{t / 10:.3f},{32 if 10 <= t < 50 else 0}" for t in range(0, 80)]
        buf = io.StringIO()
        r = main.replay_cv(main.read_cv_records(lines), out=buf)
        self.assertIn("ALERT HANDS_OFF_WHEEL VISUAL", buf.getvalue())
        self.assertIn("ALERT HANDS_OFF_WHEEL AUDIO", buf.getvalue())
        self.assertIn("CLEAR HANDS_OFF_WHEEL (ended", buf.getvalue())
        self.assertEqual(r.actions["HANDS_OFF_WHEEL"].count, 1)
        self.assertLess(r.score, 100.0)


if __name__ == "__main__":
    unittest.main()
