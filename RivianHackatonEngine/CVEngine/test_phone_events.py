import unittest

import cv2
import numpy as np

import phone_events as pe


def kp(**pts):
    k = np.full((17, 3), np.nan, np.float32)
    k[pe.LSHO], k[pe.RSHO] = [1000, 800, .9], [1300, 800, .9]          # shoulder width 300 px
    k[pe.NOSE], k[pe.LEAR] = [1150, 600, .9], [1080, 590, .9]
    for name, xy in pts.items():
        k[getattr(pe, name)] = [*xy, .9]
    return k


def phone(x, y, conf=0.6):
    return (x - 30, y - 50, x + 30, y + 50, conf)


def ev(t, state):
    return pe.Evidence(t, state, 'test')


class EvidenceTests(unittest.TestCase):
    def test_no_driver_is_unknown_and_no_phone_is_negative(self):
        e = pe.PhoneEvidence()
        self.assertEqual(e.update(0, None, [phone(900, 900)]).state, pe.UNKNOWN)
        self.assertEqual(e.update(0, kp(LWR=(1100, 1200)), []).state, pe.NEGATIVE)

    def test_phone_at_ear_needs_a_hand(self):
        e = pe.PhoneEvidence()
        self.assertEqual(e.update(0, kp(LWR=(1090, 650)), [phone(1085, 610)]).state, pe.POSITIVE)
        no_hand = e.update(1, kp(LWR=(1100, 1300)), [phone(1085, 610)])           # box near head, hand far
        self.assertEqual(no_hand.state, pe.NEGATIVE)

    def test_phone_lying_still_next_to_hand_is_not_use(self):
        e = pe.PhoneEvidence()
        states = [e.update(t * 0.33, kp(RWR=(900 + 3 * (t % 2), 1250)), [phone(850, 1400)]).state for t in range(10)]
        self.assertEqual(states[0], pe.UNKNOWN)                                    # movement not known yet
        self.assertEqual(states[-1], pe.NEGATIVE)
        self.assertNotIn(pe.POSITIVE, states)

    def test_phone_moving_in_hand_is_use(self):
        e = pe.PhoneEvidence()
        states = []
        for t in range(10):
            x, y = 1050 + 12 * t, 1300 - 6 * t                                    # hand and phone move together
            states.append(e.update(t * 0.33, kp(RWR=(x, y - 60)), [phone(x, y)]).state)
        self.assertEqual(states[-1], pe.POSITIVE)

    def test_camera_shake_does_not_make_a_lying_phone_move(self):
        e = pe.PhoneEvidence()
        states = []
        for t in range(10):
            shake = (25 * (-1) ** t, 15 * (-1) ** t)                               # whole image jumps
            states.append(e.update(t * 0.33, kp(RWR=(900 + shake[0], 1250 + shake[1])),
                                   [phone(850 + shake[0], 1400 + shake[1])], cabin_shift=shake).state)
        self.assertNotIn(pe.POSITIVE, states)

    def look_down(self, hands, n=10, conf=0.6):
        e = pe.PhoneEvidence()
        states = []
        for t in range(n):
            k = kp(RWR=(900, 1250))
            k[pe.NOSE] = [1150, 680, .9]                                            # nose 0.3 sw below the ear
            states.append(e.update(t * 0.33, k, [phone(850, 1400, conf)], hands=hands))
        return states

    def test_phone_held_still_while_looking_at_it_is_use(self):
        fingers = np.array([(850 + dx, 1380 + dy) for dx in (-20, 0, 20) for dy in (-30, 0, 30)] * 3)[:21]
        states = self.look_down({'R': dict(wrist=(900, 1250), points=fingers)})
        self.assertTrue(all(s.state == pe.POSITIVE for s in states))
        weak = self.look_down({'R': dict(wrist=(900, 1250), points=fingers)}, conf=0.2)  # e.g. wheel grip pad
        self.assertNotIn(pe.POSITIVE, [s.state for s in weak])

    def test_looking_down_with_hand_off_the_phone_is_not_use(self):
        on_wheel = np.full((21, 2), (1300.0, 1100.0))                               # hand found elsewhere
        states = self.look_down({'R': dict(wrist=(900, 1250), points=on_wheel)})
        self.assertNotIn(pe.POSITIVE, [s.state for s in states])
        self.assertEqual(states[-1].state, pe.NEGATIVE)

    def test_looking_down_without_hand_landmarks_is_never_positive(self):
        for hands in (None, {'R': dict(wrist=(900, 1250), points=None)}):
            self.assertNotIn(pe.POSITIVE, [s.state for s in self.look_down(hands)])

    def palm_frames(self, phone_xy, head_down=True):
        e = pe.PhoneEvidence()
        out = []
        for t in range(10):
            k = kp(RWR=(900, 1250), REL=(850, 1000))                               # forearm points down
            if head_down:
                k[pe.NOSE] = [1150, 680, .9]
            out.append(e.update(t * 0.33, k, [phone(*phone_xy, conf=0.2)], hands={'R': dict(wrist=(900, 1250), points=None)}))
        return [s.state for s in out]

    def test_still_phone_in_palm_with_hidden_fingers_is_use_when_looked_at(self):
        self.assertEqual(self.palm_frames((930, 1400))[-1], pe.POSITIVE)          # 0.5 sw past the wrist
        self.assertNotIn(pe.POSITIVE, self.palm_frames((930, 1400), head_down=False))

    def test_still_phone_beside_or_behind_the_wrist_is_not_in_palm(self):
        self.assertNotIn(pe.POSITIVE, self.palm_frames((1060, 1290)))             # to the side
        self.assertNotIn(pe.POSITIVE, self.palm_frames((870, 1130)))              # behind the wrist

    def test_hand_search_runs_only_when_needed(self):
        calls = []
        e = pe.PhoneEvidence()
        e.update(0, kp(RWR=(1200, 1250)), [phone(1700, 1450)], hands=lambda: calls.append(1) or {})
        self.assertEqual(calls, [])                                                 # phone far from hand
        self.look_down(lambda: calls.append(1) or {}, n=1)
        self.assertEqual(calls, [1])

    def test_phone_far_from_hand_and_ear_is_negative(self):
        e = pe.PhoneEvidence()
        self.assertEqual(e.update(0, kp(RWR=(1200, 1250)), [phone(1700, 1450)]).state, pe.NEGATIVE)

    def test_big_phone_held_flat_counts_by_its_near_edge(self):
        e = pe.PhoneEvidence()
        states = []
        for t in range(10):
            x = 1100 + 15 * t                                                      # moving with the hand
            big = (x, 1300, x + 240, 1390, 0.9)                                    # 0.8 sw wide, held at its end
            states.append(e.update(t * 0.33, kp(RWR=(x - 60, 1340)), [big]).state)  # center 0.6 sw, edge 0.2 sw
        self.assertEqual(states[-1], pe.POSITIVE)


class CabinMotionTests(unittest.TestCase):
    def test_recovers_image_shift(self):
        rng = np.random.default_rng(0)
        base = cv2.GaussianBlur((rng.random((400, 1600)) * 255).astype(np.uint8), (0, 0), 3)
        frame = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
        moved = np.roll(np.roll(frame, 7, axis=1), -4, axis=0)
        m = pe.CabinMotion(region=(slice(50, 300), slice(300, 1200)))
        self.assertEqual(m.shift(frame), (0.0, 0.0))
        dx, dy = m.shift(moved)
        self.assertAlmostEqual(dx, 7, delta=0.5)
        self.assertAlmostEqual(dy, -4, delta=0.5)


class TrackerTests(unittest.TestCase):
    def feed(self, states, step=0.33, start=0.0):
        tr, updates = pe.PhoneUseTracker(), []
        for i, s in enumerate(states):
            updates += [(k, round(start + i * step, 2)) for k, _ in tr.update(ev(start + i * step, s))]
        updates += [(k, 'end-of-clip') for k, _ in tr.finish(start + len(states) * step)]
        return tr, updates

    def test_sustained_use_starts_once_and_ends_after_gap(self):
        P, N = pe.POSITIVE, pe.NEGATIVE
        tr, updates = self.feed([N] * 3 + [P] * 12 + [N] * 12)
        self.assertEqual([k for k, _ in updates], ['start', 'end'])
        e = tr.events[0]
        self.assertAlmostEqual(e.onset_s, 0.99, places=2)
        self.assertEqual(e.end_reason, 'phone no longer in use')
        self.assertGreater(e.end_s - e.last_supported_s, 2.0)                    # not ended by a short gap

    def test_generic_tracker_uses_its_action(self):
        tr = pe.EventTracker('EATING')
        for i in range(8):
            tr.update(ev(i * 0.33, pe.POSITIVE))
        self.assertEqual(tr.active.action, 'EATING')
        self.assertEqual(pe.PhoneUseTracker().action, pe.ACTION)

    def test_single_false_frames_never_start_an_event(self):
        P, N = pe.POSITIVE, pe.NEGATIVE
        _, updates = self.feed([N, N, P, N, N, N, P, N, N, N, P, N, N] * 3)
        self.assertEqual(updates, [])

    def test_short_dropouts_do_not_end_an_event(self):
        P, N = pe.POSITIVE, pe.NEGATIVE
        tr, updates = self.feed([P] * 8 + [N, P, P, N, P] * 4 + [N] * 6 + [P] * 6)    # incl. a 2 s gap
        self.assertEqual([k for k, _ in updates], ['start', 'end'])
        self.assertEqual(tr.events[0].end_reason, 'clip end')

    def test_unknown_frames_neither_start_nor_end_but_long_loss_ends(self):
        P, U = pe.POSITIVE, pe.UNKNOWN
        _, updates = self.feed([U] * 20)
        self.assertEqual(updates, [])
        tr, updates = self.feed([P] * 8 + [U] * 15)
        self.assertEqual([k for k, _ in updates], ['start', 'end'])
        self.assertEqual(tr.events[0].end_reason, 'observation lost')

    def test_time_going_backwards_resets(self):
        tr = pe.PhoneUseTracker()
        for i in range(8):
            tr.update(ev(10 + i * 0.33, pe.POSITIVE))
        self.assertIsNotNone(tr.active)
        tr.update(ev(1.0, pe.NEGATIVE))
        self.assertIsNone(tr.active)


if __name__ == '__main__':
    unittest.main()
