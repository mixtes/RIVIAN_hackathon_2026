import unittest

import numpy as np

from occupancy import OccupancyCounter, find_people

W, H = 1920, 1536


def kp_for(box, n_visible=12, conf=0.9):
    """17 keypoints spread inside the box; the first n_visible are visible (head points come first)."""
    x0, y0, x1, y1 = box
    kp = np.zeros((17, 3))
    for i in range(17):
        kp[i] = (x0 + (x1 - x0) * (0.3 + 0.4 * (i % 3) / 2), y0 + (y1 - y0) * (0.05 + 0.9 * i / 16),
                 conf if i < n_visible else 0.1)
    return kp


def det(boxes, confs, n_visible=12):
    boxes = [list(b) for b in boxes]
    return (np.array(boxes, float), np.array(confs, float),
            np.array([kp_for(b, n_visible) for b in boxes]) if boxes else np.zeros((0, 17, 3)))


DRIVER = (793, 496, 1684, 1536)
REAR_A = (664, 418, 911, 1018)
REAR_B = (375, 420, 634, 1035)
FRONT = (100, 700, 620, 1536)


def run(counter, per_frame, dt=0.14, t0=0.0):
    out, t = [], t0
    for d in per_frame:
        out.append(counter.update(t, *d, (W, H)))
        t += dt
    return out


class FindPeopleTests(unittest.TestCase):
    def test_zones(self):
        people = find_people(*det([DRIVER, REAR_A, REAR_B, FRONT], [0.95, 0.76, 0.58, 0.8]), (W, H))
        zones = sorted(p.zone for p in people)
        self.assertEqual(zones, ['driver', 'front', 'rear', 'rear'])

    def test_duplicate_boxes_are_one_person(self):
        dup = (380, 420, 615, 800)            # inside REAR_B
        people = find_people(*det([DRIVER, REAR_B, dup], [0.95, 0.58, 0.45]), (W, H))
        self.assertEqual(len(people), 2)

    def test_rear_passenger_next_to_wide_driver_box_is_kept(self):
        # real case (011124 at 13.3 s): the rear passenger's head lies within a third of the driver's box width
        driver = (803, 483, 1687, 1536)
        rear = (664, 415, 928, 1013)
        people = find_people(*det([driver, rear], [0.93, 0.73]), (W, H))
        self.assertEqual(sorted(p.zone for p in people), ['driver', 'rear'])

    def test_low_confidence_and_few_keypoints_dropped(self):
        self.assertEqual(len(find_people(*det([DRIVER, REAR_A], [0.95, 0.2]), (W, H))), 1)
        self.assertEqual(len(find_people(*det([DRIVER, REAR_A], [0.95, 0.9], n_visible=3), (W, H))), 0)

    def test_empty(self):
        self.assertEqual(find_people(*det([], []), (W, H)), [])


class SmoothingTests(unittest.TestCase):
    def test_steady_three_people(self):
        c = OccupancyCounter()
        out = run(c, [det([DRIVER, REAR_A, REAR_B], [0.95, 0.76, 0.58])] * 30)
        self.assertEqual(out[-1].count, 3)
        self.assertEqual(out[-1].zones, {'driver': 1, 'front': 0, 'rear': 2})

    def test_missed_rear_passenger_does_not_flicker(self):
        c = OccupancyCounter()
        with_two = det([DRIVER, REAR_A, REAR_B], [0.95, 0.76, 0.58])
        without = det([DRIVER, REAR_A], [0.95, 0.76])
        seq = [with_two] * 15 + [without, without, with_two, without, with_two] + [with_two] * 10
        out = run(c, seq)
        self.assertTrue(all(o.count == 3 for o in out[10:]))

    def test_one_frame_ghost_is_ignored(self):
        c = OccupancyCounter()
        one = det([DRIVER], [0.95])
        ghost = det([DRIVER, REAR_A], [0.95, 0.6])
        out = run(c, [one] * 20 + [ghost] + [one] * 20)
        self.assertEqual(max(o.count for o in out[10:]), 1)

    def test_person_leaving_is_dropped_after_a_few_seconds(self):
        c = OccupancyCounter()
        both = det([DRIVER, REAR_A], [0.95, 0.76])
        alone = det([DRIVER], [0.95])
        out = run(c, [both] * 20 + [alone] * 60)
        self.assertEqual(out[19].count, 2)
        self.assertEqual(out[-1].count, 1)

    def test_empty_cabin_is_zero(self):
        c = OccupancyCounter()
        out = run(c, [det([], [])] * 10)
        self.assertEqual(out[-1].count, 0)

    def test_deterministic(self):
        seq = [det([DRIVER, REAR_A], [0.95, 0.7])] * 12 + [det([DRIVER], [0.95])] * 12
        a = [o.count for o in run(OccupancyCounter(), seq)]
        b = [o.count for o in run(OccupancyCounter(), seq)]
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
