import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

import label_review as lr
import phone_data as pd


def cand(clip, frame, max_conf=0.0, dark=0, face=0, boxes=None):
    return dict(clip=clip, frame=str(frame), time_s=str(frame / 30), brightness='80', dark_clip=str(dark),
                max_conf=str(max_conf), boxes=json.dumps(boxes or []), hand_near_face=str(face),
                driver_found='1')


class LabelFormatTests(unittest.TestCase):
    def test_round_trip_clips_and_drops_tiny_boxes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'a.txt'
            n = pd.write_yolo(path, [(10, 20, 50, 80), (-5, -5, 30, 30), (5, 5, 6, 6)], 100, 100)
            self.assertEqual(n, 2)
            boxes = pd.read_yolo(path, 100, 100)
            np.testing.assert_allclose(boxes[0], (10, 20, 50, 80), atol=1e-3)
            np.testing.assert_allclose(boxes[1], (0, 0, 30, 30), atol=1e-3)
            pd.write_yolo(path, [], 100, 100)
            self.assertEqual(path.read_text(), '')
            self.assertEqual(pd.read_yolo(path, 100, 100), [])
            self.assertEqual(pd.read_yolo(Path(d) / 'missing.txt', 100, 100), [])

    def test_iou(self):
        self.assertAlmostEqual(pd.iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)
        self.assertAlmostEqual(pd.iou((0, 0, 10, 10), (5, 0, 15, 10)), 1 / 3)
        self.assertEqual(pd.iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)

    def test_phone_class_ids_for_coco_and_fine_tuned_models(self):
        self.assertEqual(pd.phone_class_ids(SimpleNamespace(names={0: 'person', 67: 'cell phone'})), [67])
        self.assertEqual(pd.phone_class_ids(SimpleNamespace(names={0: 'phone'})), [0])
        with self.assertRaises(ValueError):
            pd.phone_class_ids(SimpleNamespace(names={0: 'person'}))

    def test_hand_near_face(self):
        kp = np.full((17, 3), np.nan, np.float32)
        kp[pd.LSHO], kp[pd.RSHO] = [1000, 800, .9], [1300, 800, .9]
        kp[pd.NOSE] = [1150, 600, .9]
        kp[pd.LWR] = [1160, 620, .9]
        self.assertTrue(pd.hand_near_face(kp))
        kp[pd.LWR] = [1160, 1300, .9]
        self.assertFalse(pd.hand_near_face(kp))
        self.assertFalse(pd.hand_near_face(None))


class SelectionAndSplitTests(unittest.TestCase):
    def test_priority_budget_and_no_duplicates(self):
        cands = ([cand('a', i, max_conf=0.5) for i in range(5)] +
                 [cand('b', i, dark=1) for i in range(5)] +
                 [cand('c', i, face=1) for i in range(10)] +
                 [cand('d', i) for i in range(50)])
        chosen = pd.select_frames(cands, budget=20, max_hard_negatives=4, max_dark=3)
        reasons = [r for _, r in chosen]
        self.assertEqual(len(chosen), 20)
        self.assertEqual(reasons.count('likely_phone'), 5)
        self.assertEqual(reasons.count('dark_clip'), 3)
        self.assertEqual(reasons.count('hand_at_face'), 4)
        self.assertEqual(reasons.count('random'), 8)
        keys = [(c['clip'], c['frame']) for c, _ in chosen]
        self.assertEqual(len(keys), len(set(keys)))

    def test_budget_smaller_than_likely_phones_spreads_over_them(self):
        cands = [cand('a', i, max_conf=0.5) for i in range(100)]
        chosen = pd.select_frames(cands, budget=10)
        frames = [int(c['frame']) for c, _ in chosen]
        self.assertEqual(len(frames), 10)
        self.assertEqual((frames[0], frames[-1]), (0, 99))

    def test_split_never_puts_a_driver_in_two_splits(self):
        drivers = {f'clip{i:02d}': f'D{i % 7}' for i in range(40)}
        split = pd.assign_splits(drivers)
        self.assertEqual(set(split), {f'D{i}' for i in range(7)})
        self.assertEqual(set(split.values()), {'train', 'val', 'test'})
        self.assertEqual(split, pd.assign_splits(drivers))
        test_clips = sum(split[d] == 'test' for d in drivers.values())
        self.assertGreaterEqual(test_clips, 0.2 * 40)

    def test_explicit_splits_and_errors(self):
        drivers = {'a': 'D1', 'b': 'D1', 'c': 'D2', 'd': 'D3'}
        self.assertEqual(pd.assign_splits(drivers, ['D2'], ['D3']), {'D1': 'train', 'D2': 'test', 'D3': 'val'})
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            pd.assign_splits(drivers, ['D9'])
        with self.assertRaisesRegex(ValueError, 'no driver id'):
            pd.assign_splits({'a': ''})

    def test_darken_keeps_shape_and_reaches_dark_level(self):
        rng = np.random.default_rng(0)
        img = np.full((100, 120, 3), 160, np.uint8)
        img[20:40, 30:60] = 250
        for _ in range(5):
            out = pd.darken(img, rng)
            self.assertEqual((out.shape, out.dtype), (img.shape, np.uint8))
            self.assertLess(out.mean(), 45)
            self.assertGreater(out.mean(), 10)


class DatasetFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'ds'
        self.videos = self.root / 'videos'
        self.videos.mkdir()
        for clip in ('c1', 'c2'):
            w = cv2.VideoWriter(str(self.videos / f'{clip}_video_inCabin.mp4'), cv2.VideoWriter_fourcc(*'mp4v'),
                                30, (pd.CX0 + 160, pd.CY0 + 120))
            for i in range(12):
                w.write(np.full((pd.CY0 + 120, pd.CX0 + 160, 3), 100 + i, np.uint8))
            w.release()
        self.out.mkdir()
        rows = [cand('c1', 2, max_conf=0.6, boxes=[[10, 10, 60, 50, 0.6], [100, 80, 110, 90, 0.07]]),
                cand('c1', 8), cand('c2', 4, face=1), cand('c2', 9, dark=1)]
        with open(self.out / 'candidates.csv', 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        self.drivers = self.root / 'drivers.json'
        self.drivers.write_text(json.dumps({'c1': 'D1', 'c2': 'D2'}))

    def build(self):
        args = SimpleNamespace(out=str(self.out), videos=str(self.videos), budget=10, max_hard_negatives=5,
                               max_dark=5, test_drivers=['D2'], val_drivers=None)
        with patch.object(pd, 'DRIVERS', self.drivers), patch('builtins.print'):
            pd.cmd_build(args)


class DatasetTests(DatasetFixture):
    def test_build_writes_crops_draft_labels_and_driver_splits(self):
        self.build()
        meta = {r['image']: r for r in pd.load_meta(self.out)}
        self.assertEqual(sorted(meta), ['c1_0002.jpg', 'c1_0008.jpg', 'c2_0004.jpg', 'c2_0009.jpg'])
        img = cv2.imread(str(self.out / 'images' / 'c1_0002.jpg'))
        self.assertEqual(img.shape[:2], (120, 160))
        self.assertEqual(len(pd.read_yolo(self.out / 'labels' / 'c1_0002.txt', 160, 120)), 1)  # weak draft dropped
        self.assertEqual({meta[k]['split'] for k in meta if k.startswith('c2')}, {'test'})
        self.assertEqual({meta[k]['split'] for k in meta if k.startswith('c1')}, {'train'})
        self.assertEqual({r['reviewed'] for r in meta.values()}, {'0'})

    def test_rebuild_keeps_reviewed_labels(self):
        self.build()
        (self.out / 'labels' / 'c1_0002.txt').write_text('0 0.5 0.5 0.2 0.2\n')
        rows = pd.load_meta(self.out)
        for r in rows:
            r['reviewed'] = 1 if r['image'] == 'c1_0002.jpg' else r['reviewed']
        pd.save_meta(rows, self.out)
        self.build()
        self.assertEqual((self.out / 'labels' / 'c1_0002.txt').read_text(), '0 0.5 0.5 0.2 0.2\n')
        self.assertEqual({r['image']: r['reviewed'] for r in pd.load_meta(self.out)}['c1_0002.jpg'], '1')

    def test_lists_use_only_reviewed_frames_and_synthetic_only_in_train(self):
        self.build()
        rows = pd.load_meta(self.out)
        for r in rows:
            r['reviewed'] = 1 if r['image'] in ('c1_0002.jpg', 'c2_0004.jpg') else 0
        pd.save_meta(rows, self.out)
        with patch('builtins.print'):
            pd.cmd_darken(SimpleNamespace(out=str(self.out), copies=2, seed=0))
        counts = pd.write_lists(self.out)
        train = (self.out / 'lists' / 'train.txt').read_text().split()
        test = (self.out / 'lists' / 'test.txt').read_text().split()
        self.assertEqual(counts, dict(train=3, val=0, test=1))
        self.assertTrue(any(p.endswith('c1_0002.jpg') for p in train))
        self.assertEqual(sum('_dark' in p for p in train), 2)
        self.assertTrue(test[0].endswith('c2_0004.jpg'))
        self.assertIn('0: phone', (self.out / 'data.yaml').read_text())

    def test_evaluate_counts_found_phones_and_false_alarms(self):
        self.build()
        rows = pd.load_meta(self.out)
        for r in rows:
            r['reviewed'] = 1
        pd.save_meta(rows, self.out)
        rows = pd.load_meta(self.out)

        class FakeModel:
            names = {0: 'phone'}

            def predict(self, img, **kw):
                boxes = np.array([[12, 12, 58, 48]], np.float32)
                return [SimpleNamespace(boxes=SimpleNamespace(
                    xyxy=SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: boxes))))]

        res = pd.evaluate(FakeModel(), rows, self.out, conf=0.15)
        # the one phone frame (c1_0002) is matched; the box on the 3 phone-free frames is a false alarm
        self.assertEqual((res['pos'], res['found']), (1, 1))
        self.assertEqual((res['neg'], res['false_alarm_images']), (3, 3))
        self.assertEqual((res['hard_neg'], res['hard_false']), (1, 1))
        self.assertEqual(res['dark_pos'], 0)

    def test_pack_bundle_contains_only_reviewed_frames_and_works_standalone(self):
        import subprocess, sys, tarfile
        self.build()
        rows = pd.load_meta(self.out)
        for r in rows:
            r['reviewed'] = 1 if r['image'] in ('c1_0002.jpg', 'c2_0004.jpg') else 0
        pd.save_meta(rows, self.out)
        base = self.root / 'base.pt'
        base.write_bytes(b'weights')
        bundle = self.root / 'bundle.tar.gz'
        with patch('builtins.print'):
            pd.cmd_pack(SimpleNamespace(out=str(self.out), bundle=str(bundle), base_model=str(base)))
        with tarfile.open(bundle) as tar:
            names = set(tar.getnames())
            self.assertTrue({'phone_ft/phone_data.py', 'phone_ft/live_phone.py', 'phone_ft/base.pt',
                             'phone_ft/requirements-gpu.txt', 'phone_ft/phone_dataset/meta.csv'} <= names)
            self.assertEqual(sorted(n for n in names if '/images/' in n),
                             ['phone_ft/phone_dataset/images/c1_0002.jpg', 'phone_ft/phone_dataset/images/c2_0004.jpg'])
            self.assertFalse(any('candidates' in n or 'c1_0008' in n for n in names))
            remote = self.root / 'remote'
            tar.extractall(remote, filter='data')
        work = remote / 'phone_ft'
        self.assertEqual(len(pd.load_meta(work / 'phone_dataset')), 2)
        run = subprocess.run([sys.executable, 'phone_data.py', 'darken'], cwd=work, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        counts = pd.write_lists(work / 'phone_dataset')
        self.assertEqual(counts, dict(train=3, val=0, test=1))
        train = (work / 'phone_dataset' / 'lists' / 'train.txt').read_text().split()
        self.assertTrue(all(Path(p).is_file() and str(work.resolve()) in p for p in train))

    def test_only_new_clips_adds_new_video_frames_and_keeps_reviewed_ones(self):
        with open(self.out / 'candidates.csv', newline='') as f:
            all_rows = list(csv.DictReader(f))
        fields = list(all_rows[0])

        def write(rows):
            with open(self.out / 'candidates.csv', 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)

        write([r for r in all_rows if r['clip'] == 'c1'])
        self.build()
        rows = pd.load_meta(self.out)
        for r in rows:
            r['reviewed'] = 1
        pd.save_meta(rows, self.out)
        (self.out / 'labels' / 'c1_0002.txt').write_text('0 0.5 0.5 0.2 0.2\n')
        write(all_rows)
        args = SimpleNamespace(out=str(self.out), videos=str(self.videos), budget=1, max_hard_negatives=5,
                               max_dark=5, test_drivers=['D2'], val_drivers=None, only_new_clips=True)
        with patch.object(pd, 'DRIVERS', self.drivers), patch('builtins.print'):
            pd.cmd_build(args)
        meta = {r['image']: r for r in pd.load_meta(self.out)}
        self.assertEqual(sorted(k for k in meta if k.startswith('c2')), ['c2_0009.jpg'])   # budget 1, dark first
        self.assertEqual(meta['c2_0009.jpg']['split'], 'test')
        self.assertEqual({meta[k]['reviewed'] for k in meta if k.startswith('c1')}, {'1'})
        self.assertEqual((self.out / 'labels' / 'c1_0002.txt').read_text(), '0 0.5 0.5 0.2 0.2\n')

    def test_pack_refuses_without_reviewed_frames(self):
        self.build()
        with self.assertRaises(SystemExit):
            pd.cmd_pack(SimpleNamespace(out=str(self.out), bundle=str(self.root / 'b.tar.gz'), base_model='x.pt'))

    def test_reset_restores_drafts_and_unreviews(self):
        self.build()
        draft = (self.out / 'labels' / 'c1_0002.txt').read_text()
        (self.out / 'labels' / 'c1_0002.txt').write_text('')
        (self.out / 'labels' / 'c1_0008.txt').write_text('0 0.5 0.5 0.2 0.2\n')
        rows = pd.load_meta(self.out)
        for r in rows:
            r['reviewed'] = 1 if r['image'] in ('c1_0002.jpg', 'c1_0008.jpg', 'c2_0004.jpg') else 0
        pd.save_meta(rows, self.out)
        with patch('builtins.print'):
            pd.cmd_reset(SimpleNamespace(out=str(self.out), all_reviewed=False, images=['c1_0002', 'c1_0008']))
        meta = {r['image']: r for r in pd.load_meta(self.out)}
        self.assertEqual((self.out / 'labels' / 'c1_0002.txt').read_text(), draft)
        self.assertEqual((self.out / 'labels' / 'c1_0008.txt').read_text(), '')
        self.assertEqual([meta[k]['reviewed'] for k in ('c1_0002.jpg', 'c1_0008.jpg', 'c2_0004.jpg')], ['0', '0', '1'])
        self.assertEqual(meta['c1_0002.jpg']['n_boxes'], '1')
        with patch('builtins.print'):
            pd.cmd_reset(SimpleNamespace(out=str(self.out), all_reviewed=True, images=None))
        self.assertEqual({r['reviewed'] for r in pd.load_meta(self.out)}, {'0'})
        with self.assertRaises(SystemExit):
            pd.cmd_reset(SimpleNamespace(out=str(self.out), all_reviewed=False, images=['nope']))


class ReviewKeyAndDeleteTests(unittest.TestCase):
    def test_read_key_normalizes_case_and_delete_keys(self):
        for code in (ord('d'), ord('D'), 8, 0x2E0000, 0xFFFF):
            self.assertEqual(lr.read_key(code), lr.DELETE)
        self.assertEqual(lr.read_key(ord('C')), ord('c'))
        self.assertEqual(lr.read_key(ord(' ')), ord(' '))
        self.assertEqual(lr.read_key(-1), -1)
        self.assertEqual(lr.read_key(0x250000), -1)        # arrow key ignored

    def test_delete_selected_then_under_mouse_then_only_box(self):
        s = lr.Session('.', [])
        s.boxes = [(0, 0, 10, 10), (50, 50, 80, 80)]
        self.assertFalse(s.delete())                          # nothing selected, no mouse, 2 boxes
        self.assertTrue(s.delete(60, 60))                     # box under the mouse
        self.assertEqual(s.boxes, [(0, 0, 10, 10)])
        self.assertTrue(s.delete(500, 500))                   # mouse elsewhere: the only box
        self.assertEqual(s.boxes, [])
        self.assertFalse(s.delete(5, 5))

    def test_find_start_by_frame_or_clip(self):
        rows = [dict(image=n) for n in ('a_0005.jpg', 'b_0005.jpg', 'b_0035.jpg', 'c_0005.jpg')]
        self.assertEqual(lr.find_start(rows, 'b_0035'), 2)
        self.assertEqual(lr.find_start(rows, 'b_0035.jpg'), 2)
        self.assertEqual(lr.find_start(rows, 'b'), 1)
        self.assertIsNone(lr.find_start(rows, 'z'))

    def test_dedupe_keeps_one_box_per_phone(self):
        drafts = [(503, 506, 743, 672, 0.4), (523, 558, 740, 672, 0.3), (487, 519, 667, 657, 0.2),
                  (100, 100, 150, 180, 0.15)]                 # real triple from the dataset + a separate phone
        kept = pd.dedupe_boxes(drafts)
        self.assertEqual(kept, [(503, 506, 743, 672, 0.4), (100, 100, 150, 180, 0.15)])
        self.assertEqual(pd.dedupe_boxes([]), [])


class ReviewWindowSizeTests(unittest.TestCase):
    def test_fits_small_and_large_screens_and_never_enlarges(self):
        img_w, img_h = 1160, 936                       # the real crop size
        for sw, sh in [(1366, 768), (1280, 720), (1920, 1080), (1536, 864)]:
            scale = lr.fit_scale(img_w, img_h, sw, sh, bar_h=80)
            self.assertLessEqual(img_w * scale, 0.9 * sw + 1)
            self.assertLessEqual(img_h * scale + 80, 0.8 * sh + 1)
        self.assertEqual(lr.fit_scale(400, 300, 3840, 2160, 80), 1.0)
        self.assertAlmostEqual(lr.fit_scale(400, 300, 3840, 2160, 80, zoom=0.5), 0.5)

    def test_status_bar_text_fits_narrow_windows(self):
        for width in (500, 900, 1400):
            bar, h = lr.status_bar(width, [('x' * 90, (255, 255, 255)), (lr.HELP, (0, 255, 0))])
            self.assertEqual(bar.shape, (h, width, 3))
            self.assertGreater(h, 30)


class ReviewSessionTests(DatasetFixture):
    def test_session_edit_confirm_and_navigation(self):
        self.build()
        rows = pd.load_meta(self.out)
        s = lr.Session(self.out, rows)
        s.load(0)
        self.assertEqual(len(s.boxes), 1)
        self.assertTrue(s.select_at(30, 30))
        s.delete_selected()
        self.assertFalse(s.add_box(5, 5, 8, 8))
        self.assertTrue(s.add_box(80, 70, 20, 10))
        self.assertEqual(s.boxes[-1], (20, 10, 80, 70))
        self.assertEqual(s.confirm(), 1)
        meta = {r['image']: r for r in pd.load_meta(self.out)}
        self.assertEqual((meta['c1_0002.jpg']['reviewed'], meta['c1_0002.jpg']['n_boxes']), ('1', '1'))
        self.assertEqual(s.next_unreviewed(0), 1)
        s.load(1)
        s.clear()
        self.assertEqual(s.confirm(), 0)
        self.assertEqual((self.out / 'labels' / 'c1_0008.txt').read_text(), '')
        self.assertEqual(len(lr.filter_rows(pd.load_meta(self.out), 'unreviewed')), 2)
        self.assertEqual(len(lr.filter_rows(pd.load_meta(self.out), 'dark')), 1)


if __name__ == '__main__':
    unittest.main()
