"""AlertEngine acceptance tests (AlertEngine/CAR_FRONTEND_INTERFACE.md section 8).

Run from RivianHackatonEngine/:  python -m unittest discover tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from AlertEngine.AlertEngine import (ACTIONS, ActionAlertConfig, Alert, AlertClear,  # noqa: E402
                                     AlertEngine, AlertLevel, EscalationStep)


def _tick_until(engine, t0, t1, dt=0.1):
    out, t = [], t0
    while t <= t1 + 1e-9:
        out += engine.tick_all(round(t, 3))
        t += dt
    return out


class AcceptanceTests(unittest.TestCase):

    def test_1_first_step_at_zero(self):
        out = AlertEngine().process_event("phone_call", True, 0.0)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Alert)
        self.assertEqual(out[0].action, "PHONE_CALL")
        self.assertEqual(out[0].level, AlertEngine().config["PHONE_CALL"].steps[0].level)

    def test_2_one_alert_per_step_same_id(self):
        for action in ACTIONS:
            e = AlertEngine()
            steps = e.config[action].steps
            alerts = e.process_event(action, True, 0.0)
            alerts += _tick_until(e, 0.1, steps[-1].after_seconds + 2)
            self.assertEqual(len(alerts), len(steps), action)
            self.assertEqual(len({a.alert_id for a in alerts}), 1, action)
            levels = [a.level for a in alerts]
            self.assertEqual(levels, sorted(set(levels)), action)

    def test_3_two_actions_same_tick(self):
        e = AlertEngine()
        e.process_event("PHONE_CALL", True, 0.0)
        e.process_event("HANDS_OFF_WHEEL", True, 0.0)
        out = e.tick_all(3.0)
        self.assertEqual({a.action for a in out}, {"PHONE_CALL", "HANDS_OFF_WHEEL"})

    def test_4_end_clears_once(self):
        e = AlertEngine()
        [alert] = e.process_event("PHONE_CALL", True, 0.0)
        out = e.process_event("PHONE_CALL", False, 2.0)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], AlertClear)
        self.assertEqual(out[0].reason, "ended")
        self.assertEqual(out[0].alert_id, alert.alert_id)
        self.assertEqual(out[0].duration, 2.0)
        self.assertEqual(e.process_event("PHONE_CALL", False, 3.0), [])
        self.assertEqual(e.active_alerts(), [])

    def test_5_restart_new_id(self):
        e = AlertEngine()
        [a1] = e.process_event("PHONE_CALL", True, 0.0)
        e.process_event("PHONE_CALL", False, 1.0)
        [a2] = e.process_event("PHONE_CALL", True, 2.0)
        self.assertNotEqual(a1.alert_id, a2.alert_id)

    def test_6_latched_eyes_closed(self):
        e = AlertEngine()
        e.process_event("EYES_CLOSED", True, 0.0)
        [alert] = e.tick_all(1.0)
        self.assertTrue(alert.requires_ack)
        self.assertEqual(e.process_event("EYES_CLOSED", False, 2.0), [])
        self.assertEqual([a.alert_id for a in e.active_alerts()], [alert.alert_id])
        self.assertEqual(e.tick_all(5.0), [])
        [clear] = e.dismiss(alert.alert_id, 10.0)
        self.assertIsInstance(clear, AlertClear)
        self.assertEqual(clear.reason, "dismissed")
        self.assertEqual(clear.duration, 2.0)
        self.assertEqual(e.dismiss(alert.alert_id, 11.0), [])
        self.assertEqual(e.active_alerts(), [])

    def test_7_dismiss_while_active_starts_new_occurrence(self):
        e = AlertEngine()
        e.process_event("EYES_CLOSED", True, 0.0)
        [alert] = e.tick_all(1.0)
        out = e.dismiss(alert.alert_id, 2.0)
        self.assertIsInstance(out[0], AlertClear)
        self.assertEqual(out[0].alert_id, alert.alert_id)
        e.process_event("EYES_CLOSED", True, 2.1)       # idempotent, same new occurrence
        [again] = e.tick_all(2.6)
        self.assertNotEqual(again.alert_id, alert.alert_id)
        self.assertAlmostEqual(again.duration_so_far, 0.6)

    def test_8_unknown_action(self):
        e = AlertEngine()
        self.assertEqual(e.process_event("NOT_A_THING", True, 0.0), [])
        self.assertEqual(e.process_event(None, True, 0.0), [])  # type: ignore[arg-type]
        self.assertEqual(e.dismiss("no-such-id", 0.0), [])

    def test_9_all_actions_configured(self):
        import main
        cfg = AlertEngine().config
        self.assertEqual(set(cfg), set(ACTIONS))
        self.assertEqual(len(ACTIONS), 8)
        self.assertTrue(set(main.DETECTED_ACTIONS) <= set(cfg))

    def test_10_import_main_isolated(self):
        code = "import main; s = main.SafetySystem(); print(main.__version__)"
        r = subprocess.run([sys.executable, "-I", "-S", "-c",
                            f"import sys; sys.path.insert(0, {ROOT!r}); {code}"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class ExtraTests(unittest.TestCase):

    def test_repeated_true_is_idempotent(self):
        e = AlertEngine()
        self.assertEqual(e.process_event("HANDS_OFF_WHEEL", True, 0.0), [])
        [a] = e.process_event("HANDS_OFF_WHEEL", True, 1.0)
        self.assertEqual(e.process_event("HANDS_OFF_WHEEL", True, 1.5), [])
        self.assertEqual(a.duration_so_far, 1.0)

    def test_end_before_first_step_no_clear(self):
        e = AlertEngine()
        e.process_event("HANDS_OFF_WHEEL", True, 0.0)
        self.assertEqual(e.process_event("HANDS_OFF_WHEEL", False, 0.5), [])

    def test_dismiss_non_latched_ignored(self):
        e = AlertEngine()
        [a] = e.process_event("PHONE_CALL", True, 0.0)
        self.assertEqual(e.dismiss(a.alert_id, 1.0), [])
        self.assertEqual(len(e.active_alerts()), 1)

    def test_reset_emits_nothing(self):
        e = AlertEngine()
        e.process_event("PHONE_CALL", True, 0.0)
        e.reset()
        self.assertEqual(e.active_alerts(), [])
        self.assertEqual(e.active_actions(), [])
        self.assertEqual(e.process_event("PHONE_CALL", False, 1.0), [])

    def test_custom_config_and_no_none_levels(self):
        e = AlertEngine({"phone_call": ActionAlertConfig(
            [EscalationStep(2.0, AlertLevel.AUDIO, "b"),
             EscalationStep(0.0, AlertLevel.VISUAL, "a")])})
        self.assertEqual(e.process_event("PHONE_CALL", True, 0.0)[0].message, "a")
        self.assertEqual(e.tick_all(2.0)[0].level, AlertLevel.AUDIO)
        for cfg in AlertEngine().config.values():
            self.assertTrue(all(s.level > AlertLevel.NONE and s.message for s in cfg.steps))

    def test_messages_short(self):
        for cfg in AlertEngine().config.values():
            for s in cfg.steps:
                self.assertLessEqual(len(s.message), 40, s.message)

    def test_safety_system_dismiss(self):
        import main
        s = main.SafetySystem()
        s.start_drive(0.0)
        s.on_event("EYES_CLOSED", True, 1.0)
        [a] = s.tick_all(2.0)
        s.on_event("EYES_CLOSED", False, 3.0)
        [c] = s.dismiss(a.alert_id, 4.0)
        self.assertEqual(c.reason, "dismissed")
        report = s.end_drive(10.0)
        self.assertTrue(0 <= report.score <= 100)


if __name__ == "__main__":
    unittest.main()
