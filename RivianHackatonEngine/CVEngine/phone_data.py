"""Phone-detector fine-tuning: frame selection, pre-labels, dataset, training, evaluation.

Images are the driver-side crop used by live_phone.py (x >= CX0, y >= CY0), so the
model is trained on exactly what it sees live. Only frames a person has reviewed
(label_review.py) are ever used for training or evaluation; pre-labels are drafts.
The dataset contains images of real people: keep it local (phone_dataset/ is git-ignored).

Workflow (run from the CVEngine folder):
  1. python phone_data.py contact-sheet        # one frame per clip, to group clips by driver
     -> fill phone_drivers.json  {clip: driver_id}
  2. python phone_data.py prelabel             # 1 frame/s (3/s in dark clips), draft boxes
  3. python phone_data.py build --test-drivers D03 D04 D07 D13 --val-drivers D05 D08 D09 D10 D16
     (after adding videos: same command + --only-new-clips, so reviewed frames are kept and only new clips are sampled)
  4. python label_review.py                    # correct the draft boxes
  5. python phone_data.py darken               # synthetic dark copies of reviewed train frames
  6. python phone_data.py train                # fine-tune (GPU strongly recommended)
  7. python phone_data.py eval --model phone_dataset/runs/phone_ft/weights/best.pt
  python phone_data.py status                  # progress at any time
  python phone_data.py pack                    # bundle reviewed frames to train on a remote GPU (steps 5-7 there)
"""
import argparse, csv, hashlib, json, os, sys, time
from pathlib import Path

import cv2
import numpy as np

from live_phone import (CX0, CY0, KP_CONF, LEAR, LSHO, LWR, NOSE, REAR, RSHO, RWR, phone_class_ids,
                        select_driver)

VIDEO_DIR = Path('videos') / 'Hackathon dataset 2026'
OUT = Path('phone_dataset')
DRIVERS = Path('phone_drivers.json')
PRELABEL_MODEL = 'yolo11x.pt'        # strongest available COCO detector: drafts only, never ground truth
PRELABEL_CONF = 0.05                 # record weak candidates too, so the reviewer can see them
DRAFT_BOX_CONF = 0.10                # weaker candidates are not drawn as draft boxes
DARK_BRIGHTNESS = 45                 # mean gray level of the driver crop; the missed dark clip is ~27
SAMPLE_FPS, DARK_SAMPLE_FPS = 1, 3
META_FIELDS = ['image', 'clip', 'frame', 'time_s', 'driver', 'split', 'reason', 'brightness',
               'dark_clip', 'prelabel_conf', 'reviewed', 'n_boxes']


# ---- small helpers (pure, tested) -------------------------------------------
def crop(frame):
    return frame[CY0:, CX0:]


def image_id(clip, frame):
    return f'{clip}_{int(frame):04d}'


def write_yolo(path, boxes, w, h):
    """boxes: [(x1, y1, x2, y2), ...] in pixels -> YOLO txt (class 0 = phone). Empty file = no phone."""
    lines = []
    for x1, y1, x2, y2 in boxes:
        x1, x2 = sorted((max(0.0, min(w, x1)), max(0.0, min(w, x2))))
        y1, y2 = sorted((max(0.0, min(h, y1)), max(0.0, min(h, y2))))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        lines.append(f'0 {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}')
    Path(path).write_text('\n'.join(lines) + ('\n' if lines else ''))
    return len(lines)


def read_yolo(path, w, h):
    boxes = []
    if Path(path).is_file():
        for line in Path(path).read_text().splitlines():
            parts = line.split()
            if len(parts) == 5:
                _, cx, cy, bw, bh = map(float, parts)
                boxes.append((cx * w - bw * w / 2, cy * h - bh * h / 2, cx * w + bw * w / 2, cy * h + bh * h / 2))
    return boxes


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def dedupe_boxes(boxes, iou_thr=0.4, contain_thr=0.8):
    """Keep one draft box per object: drop boxes overlapping a higher-confidence box, or mostly inside it.
    boxes: [(x1, y1, x2, y2, conf), ...] -> same format, highest confidence first."""
    kept = []
    for b in sorted(boxes, key=lambda b: -b[4]):
        dup = False
        for k in kept:
            ix = max(0.0, min(b[2], k[2]) - max(b[0], k[0]))
            iy = max(0.0, min(b[3], k[3]) - max(b[1], k[1]))
            smaller = min((b[2] - b[0]) * (b[3] - b[1]), (k[2] - k[0]) * (k[3] - k[1]))
            if iou(b, k) > iou_thr or (smaller > 0 and ix * iy / smaller > contain_thr):
                dup = True
                break
        if not dup:
            kept.append(b)
    return kept


def draft_boxes(candidate):
    """Draft boxes for one candidate row: confident enough, one per object."""
    return [b[:4] for b in dedupe_boxes([b for b in json.loads(candidate['boxes']) if b[4] >= DRAFT_BOX_CONF])]


def hand_near_face(kp):
    if kp is None:
        return False
    ok = lambda i: kp[i, 2] >= KP_CONF
    sw = np.hypot(*(kp[LSHO, :2] - kp[RSHO, :2])) if ok(LSHO) and ok(RSHO) else 350.0
    head = [kp[i, :2] for i in (LEAR, REAR, NOSE) if ok(i)]
    hands = [kp[i, :2] for i in (LWR, RWR) if ok(i)]
    return any(np.hypot(*(w - h)) < 0.8 * sw for w in hands for h in head)


def select_frames(candidates, budget=700, max_hard_negatives=120, max_dark=150, seed=0):
    """Pick frames to label, in priority order: likely phones, dark-clip frames,
    hard negatives (hand at the face, no phone candidate), then spread-out random negatives."""
    rng = np.random.default_rng(seed)
    likely = [c for c in candidates if float(c['max_conf']) >= DRAFT_BOX_CONF]
    dark = [c for c in candidates if c['dark_clip'] == '1' and c not in likely]
    hard = [c for c in candidates if c['hand_near_face'] == '1' and c not in likely and c not in dark]
    rest = [c for c in candidates if c not in likely and c not in dark and c not in hard]
    chosen = []

    def take(group, limit, reason):
        limit = max(0, min(limit, budget - len(chosen)))
        if len(group) > limit:
            group = [group[i] for i in np.linspace(0, len(group) - 1, limit).astype(int)] if limit else []
        chosen.extend((c, reason) for c in group)

    take(likely, budget, 'likely_phone')
    take(dark, max_dark, 'dark_clip')
    take(hard, max_hard_negatives, 'hand_at_face')
    take([rest[i] for i in rng.permutation(len(rest))], budget, 'random')
    return chosen


def assign_splits(drivers, test_drivers=None, val_drivers=None, test_frac=0.2, val_frac=0.15):
    """{clip: driver} -> {driver: split}. A driver is never in two splits."""
    by_driver = {}
    for clip, driver in drivers.items():
        if not driver:
            raise ValueError(f'Clip {clip} has no driver id in {DRIVERS}; group every clip first.')
        by_driver.setdefault(driver, []).append(clip)
    split = {d: 'train' for d in by_driver}
    if test_drivers or val_drivers:
        for d in test_drivers or []:
            split[d] = 'test'
        for d in val_drivers or []:
            split[d] = 'val'
        unknown = set(test_drivers or []) | set(val_drivers or [])
        unknown -= set(by_driver)
        if unknown:
            raise ValueError(f'Unknown driver ids: {sorted(unknown)}')
        return split
    total = len(drivers)
    order = sorted(by_driver, key=lambda d: hashlib.sha1(d.encode()).hexdigest())
    counts = dict(test=0, val=0)
    for d in order:
        for name, frac in (('test', test_frac), ('val', val_frac)):
            if counts[name] < frac * total:
                split[d] = name
                counts[name] += len(by_driver[d])
                break
    return split


def darken(img, rng):
    """Imitate the dark cabin clips: gamma darkening to a low mean level plus sensor noise."""
    x = (img.astype(np.float32) / 255.0) ** rng.uniform(1.6, 2.6)
    target = rng.uniform(18, 40)
    x = x * (target / max(x.mean() * 255.0, 1e-3))
    x = x * 255.0 + rng.normal(0, rng.uniform(2, 6), img.shape)
    return np.clip(x, 0, 255).astype(np.uint8)


def load_meta(out=OUT):
    path = Path(out) / 'meta.csv'
    if not path.is_file():
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def save_meta(rows, out=OUT):
    path = Path(out) / 'meta.csv'
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=META_FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


# ---- commands -----------------------------------------------------------------
def clips(video_dir):
    return sorted(p for p in Path(video_dir).glob('*.mp4'))


def cmd_contact_sheet(args):
    """One frame per clip in time order, labeled with its driver id or NEW, to group clips by driver.
    Clips missing from phone_drivers.json are added with an empty id to fill in."""
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    vids = clips(args.videos)
    drivers = json.loads(DRIVERS.read_text()) if DRIVERS.exists() else {}
    tiles = []
    for v in vids:
        cap = cv2.VideoCapture(str(v))
        cap.set(cv2.CAP_PROP_POS_FRAMES, 300)
        ok, f = cap.read()
        cap.release()
        f = cv2.resize(f if ok else np.zeros((1536, 1920, 3), np.uint8), (384, 307))
        driver = drivers.get(v.stem[:15], '')
        cv2.rectangle(f, (0, 0), (384, 30), (0, 0, 0), -1)
        cv2.putText(f, f'{v.stem[:15]}  {driver or "NEW"}', (6, 22), 0, 0.65,
                    (255, 255, 255) if driver else (0, 255, 255), 2)
        tiles.append(f)
    per_sheet, cols = 28, 7
    for old in out.glob('contact_sheet_*.jpg'):
        old.unlink()
    for s in range(0, len(tiles), per_sheet):
        chunk = tiles[s:s + per_sheet]
        chunk += [np.zeros_like(tiles[0])] * (-len(chunk) % cols)
        sheet = np.vstack([np.hstack(chunk[r:r + cols]) for r in range(0, len(chunk), cols)])
        path = out / f'contact_sheet_{s // per_sheet + 1}.jpg'
        cv2.imwrite(str(path), sheet)
        print('wrote', path)
    missing = [v.stem[:15] for v in vids if v.stem[:15] not in drivers]
    if missing or not DRIVERS.exists():
        drivers.update({c: '' for c in missing})
        DRIVERS.write_text(json.dumps(dict(sorted(drivers.items())), indent=1))
        print(f'{DRIVERS}: {len(missing)} clips need a driver id (same person = same id)')


def cmd_prelabel(args):
    from ultralytics import YOLO
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    path = out / 'candidates.csv'
    fields = ['clip', 'frame', 'time_s', 'brightness', 'dark_clip', 'max_conf', 'boxes',
              'hand_near_face', 'driver_found']
    done = set()
    if path.is_file():
        with open(path, newline='') as f:
            done = {r['clip'] for r in csv.DictReader(f)}
    pose, det = YOLO('yolo11m-pose.pt'), YOLO(args.model)
    phone_ids = phone_class_ids(det)
    new_file = not path.is_file()
    with open(path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        for v in clips(args.videos):
            clip = v.stem[:15]
            if clip in done:
                continue
            t0 = time.monotonic()
            cap = cv2.VideoCapture(str(v))
            n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS) or 30
            probe = []
            for fi in (n // 6, n // 2, 5 * n // 6):
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                ok, fr = cap.read()
                if ok:
                    probe.append(cv2.cvtColor(crop(fr), cv2.COLOR_BGR2GRAY).mean())
            dark = bool(probe) and float(np.mean(probe)) < DARK_BRIGHTNESS
            step = max(1, int(round(fps / (DARK_SAMPLE_FPS if dark else SAMPLE_FPS))))
            rows = 0
            for fi in range(step // 2, n, step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                ok, fr = cap.read()
                if not ok:
                    continue
                r = pose(fr, imgsz=640, conf=0.25, verbose=False)[0]
                kp = select_driver(r.boxes.xyxy.cpu().numpy(), r.keypoints.data.cpu().numpy(), fr.shape[1])
                c = crop(fr)
                d = det(c, imgsz=640, conf=PRELABEL_CONF, classes=phone_ids, verbose=False)[0]
                boxes = [[round(float(x), 1) for x in b] + [round(float(s), 3)]
                         for b, s in zip(d.boxes.xyxy.cpu().numpy(), d.boxes.conf.cpu().numpy())]
                w.writerow(dict(clip=clip, frame=fi, time_s=round(fi / fps, 3),
                                brightness=round(float(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY).mean()), 1),
                                dark_clip=int(dark), max_conf=max((b[4] for b in boxes), default=0.0),
                                boxes=json.dumps(boxes), hand_near_face=int(hand_near_face(kp)),
                                driver_found=int(kp is not None)))
                rows += 1
            cap.release()
            f.flush()
            print(f'{clip}: {rows} frames{" (dark clip)" if dark else ""}, {time.monotonic() - t0:.0f}s', flush=True)
    print('candidates:', path)


def cmd_build(args):
    out = Path(args.out)
    with open(out / 'candidates.csv', newline='') as f:
        candidates = list(csv.DictReader(f))
    drivers = json.loads(DRIVERS.read_text())
    missing = sorted({c['clip'] for c in candidates} - {k for k, v in drivers.items() if v})
    if missing:
        sys.exit(f'Driver id missing in {DRIVERS} for: {missing}')
    splits = assign_splits(drivers, args.test_drivers, args.val_drivers)
    meta = {r['image']: r for r in load_meta(out)}
    if getattr(args, 'only_new_clips', False):
        done_clips = {r['clip'] for r in meta.values()}
        candidates = [c for c in candidates if c['clip'] not in done_clips]
        print(f'selecting from {len({c["clip"] for c in candidates})} clips without frames in the dataset')
    moved = 0
    for r in meta.values():                           # keep every existing frame's split consistent
        new_split = splits[drivers[r['clip']]]
        moved += new_split != r['split']
        r['driver'], r['split'] = drivers[r['clip']], new_split
    if moved:
        print(f'WARNING: {moved} existing frames changed split. Pass the same --test-drivers/--val-drivers '
              'as before unless this is intended.')
    chosen = select_frames(candidates, args.budget, args.max_hard_negatives, args.max_dark)
    (out / 'images').mkdir(parents=True, exist_ok=True)
    (out / 'labels').mkdir(exist_ok=True)
    by_clip = {}
    for c, reason in chosen:
        by_clip.setdefault(c['clip'], []).append((c, reason))
    added = 0
    for clip, items in sorted(by_clip.items()):
        cap = cv2.VideoCapture(str(Path(args.videos) / f'{clip}_video_inCabin.mp4'))
        for c, reason in sorted(items, key=lambda x: int(x[0]['frame'])):
            name = image_id(clip, c['frame'])
            driver = drivers[clip]
            if f'{name}.jpg' in meta:                 # never overwrite reviewed or existing labels
                meta[f'{name}.jpg']['driver'], meta[f'{name}.jpg']['split'] = driver, splits[driver]
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(c['frame']))
            ok, fr = cap.read()
            if not ok:
                continue
            img = crop(fr)
            cv2.imwrite(str(out / 'images' / f'{name}.jpg'), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            drafts = draft_boxes(c)
            n_boxes = write_yolo(out / 'labels' / f'{name}.txt', drafts, img.shape[1], img.shape[0])
            meta[f'{name}.jpg'] = dict(image=f'{name}.jpg', clip=clip, frame=c['frame'], time_s=c['time_s'],
                              driver=driver, split=splits[driver], reason=reason,
                              brightness=c['brightness'], dark_clip=c['dark_clip'],
                              prelabel_conf=c['max_conf'], reviewed=0, n_boxes=n_boxes)
            added += 1
        cap.release()
    save_meta(sorted(meta.values(), key=lambda r: r['image']), out)
    print(f'added {added} images ({len(meta)} total) -> {out}')
    cmd_status(args)


def cmd_status(args):
    rows = load_meta(args.out)
    if not rows:
        print('no dataset yet')
        return
    print(f'{"split":6s} {"images":>7s} {"reviewed":>9s} {"with phone":>11s} {"dark":>5s}  drivers')
    for split in ('train', 'val', 'test'):
        r = [x for x in rows if x['split'] == split]
        rev = [x for x in r if x['reviewed'] == '1']
        print(f'{split:6s} {len(r):7d} {len(rev):9d} {sum(int(x["n_boxes"]) > 0 for x in rev):11d} '
              f'{sum(x["dark_clip"] == "1" for x in rev):5d}  {sorted({x["driver"] for x in r})}')
    print('reasons:', {k: sum(x['reason'] == k for x in rows) for k in sorted({x['reason'] for x in rows})})


def cmd_darken(args):
    out = Path(args.out)
    rng = np.random.default_rng(args.seed)
    rows = [r for r in load_meta(out) if r['split'] == 'train' and r['reviewed'] == '1' and r['dark_clip'] == '0']
    pos = [r for r in rows if int(r['n_boxes']) > 0]
    neg = [r for r in rows if int(r['n_boxes']) == 0]
    neg = [neg[i] for i in rng.permutation(len(neg))[:len(pos)]]   # dark negatives too: dark must not mean phone
    img_dir, lab_dir = out / 'synthetic' / 'images', out / 'synthetic' / 'labels'
    img_dir.mkdir(parents=True, exist_ok=True)
    lab_dir.mkdir(parents=True, exist_ok=True)
    for old in img_dir.glob('*.jpg'):
        old.unlink()
    for old in lab_dir.glob('*.txt'):
        old.unlink()
    made = 0
    for r in pos + neg:
        stem = Path(r['image']).stem
        img = cv2.imread(str(out / 'images' / r['image']))
        for k in range(args.copies):
            cv2.imwrite(str(img_dir / f'{stem}_dark{k}.jpg'), darken(img, rng), [cv2.IMWRITE_JPEG_QUALITY, 95])
            (lab_dir / f'{stem}_dark{k}.txt').write_text((out / 'labels' / f'{stem}.txt').read_text())
            made += 1
    print(f'{made} synthetic dark images from {len(pos)} phone + {len(neg)} no-phone reviewed train frames')


def write_lists(out):
    out = Path(out).resolve()
    rows = [r for r in load_meta(out) if r['reviewed'] == '1']
    lists = out / 'lists'
    lists.mkdir(exist_ok=True)
    counts = {}
    for split in ('train', 'val', 'test'):
        paths = [str(out / 'images' / r['image']) for r in rows if r['split'] == split]
        if split == 'train':
            paths += [str(p) for p in sorted((out / 'synthetic' / 'images').glob('*.jpg'))]
        (lists / f'{split}.txt').write_text('\n'.join(paths) + '\n')
        counts[split] = len(paths)
    (out / 'data.yaml').write_text(
        f'path: {out.as_posix()}\ntrain: lists/train.txt\nval: lists/val.txt\ntest: lists/test.txt\n'
        'names:\n  0: phone\n')
    return counts


def cmd_train(args):
    counts = write_lists(args.out)
    print('training lists (reviewed frames only):', counts)
    if counts['train'] < args.min_train or counts['val'] < 10:
        sys.exit(f'Not enough reviewed frames (need >= {args.min_train} train and 10 val). '
                 'Review more with label_review.py.')
    from ultralytics import YOLO
    import torch
    device = args.device or ('0' if torch.cuda.is_available() else 'cpu')
    if device == 'cpu':
        print('WARNING: no GPU found; training on CPU will be slow (hours).')
    YOLO(args.model).train(
        data=str(Path(args.out).resolve() / 'data.yaml'), epochs=args.epochs, imgsz=640, batch=args.batch,
        freeze=args.freeze, patience=15, device=device, workers=args.workers, seed=0,
        hsv_v=0.5, hsv_s=0.5, degrees=5, translate=0.1, scale=0.4, fliplr=0.5, mosaic=1.0, close_mosaic=10,
        project=str(Path(args.out).resolve() / 'runs'), name=args.name, exist_ok=True, plots=True)


def evaluate(model, rows, out, conf, match_iou=0.3):
    phone_ids = phone_class_ids(model)
    res = dict(pos=0, found=0, neg=0, false_alarm_images=0, dark_pos=0, dark_found=0,
               hard_neg=0, hard_false=0, seconds=0.0)
    for r in rows:
        img = cv2.imread(str(Path(out) / 'images' / r['image']))
        gt = read_yolo(Path(out) / 'labels' / (Path(r['image']).stem + '.txt'), img.shape[1], img.shape[0])
        t0 = time.monotonic()
        p = model.predict(img, imgsz=640, conf=conf, classes=phone_ids, verbose=False)[0]
        res['seconds'] += time.monotonic() - t0
        preds = [tuple(b) for b in p.boxes.xyxy.cpu().numpy()]
        hit = any(iou(g, q) >= match_iou for g in gt for q in preds)
        spurious = any(all(iou(g, q) < match_iou for g in gt) for q in preds)
        if gt:
            res['pos'] += 1
            res['found'] += hit
            if r['dark_clip'] == '1':
                res['dark_pos'] += 1
                res['dark_found'] += hit
        else:
            res['neg'] += 1
            res['false_alarm_images'] += spurious
            if r['reason'] == 'hand_at_face':
                res['hard_neg'] += 1
                res['hard_false'] += spurious
    res['ms_per_image'] = round(1000 * res.pop('seconds') / max(len(rows), 1))
    return res


def cmd_eval(args):
    from ultralytics import YOLO
    rows = [r for r in load_meta(args.out) if r['reviewed'] == '1' and r['split'] == args.split]
    if not rows:
        sys.exit(f'No reviewed {args.split} frames.')
    print(f'{len(rows)} reviewed {args.split} frames, drivers {sorted({r["driver"] for r in rows})}')
    for name in [args.baseline, args.model]:
        if not name:
            continue
        r = evaluate(YOLO(name), rows, args.out, args.conf)
        f = lambda a, b: f'{r[a]}/{r[b]}'
        print(f'{name}: phones found {f("found", "pos")}, false-alarm images {f("false_alarm_images", "neg")}, '
              f'dark phones found {f("dark_found", "dark_pos")}, hand-at-face false alarms '
              f'{f("hard_false", "hard_neg")}, {r["ms_per_image"]} ms/image')


REMOTE_REQUIREMENTS = 'ultralytics==8.4.193\n'   # pulls in torch (CUDA build on Linux), opencv, numpy


def cmd_reset(args):
    """Undo review: mark frames as not reviewed and restore their original draft boxes."""
    out = Path(args.out)
    rows = load_meta(out)
    names = {n if n.endswith('.jpg') else f'{n}.jpg' for n in (args.images or [])}
    targets = [r for r in rows if (args.all_reviewed and r['reviewed'] == '1') or r['image'] in names]
    unknown = names - {r['image'] for r in rows}
    if unknown:
        sys.exit(f'Unknown images: {sorted(unknown)}')
    if not targets:
        sys.exit('Nothing to reset: pass --all-reviewed or --images NAME ...')
    with open(out / 'candidates.csv', newline='') as f:
        drafts = {image_id(c['clip'], c['frame']): c for c in csv.DictReader(f)}
    for r in targets:
        stem = Path(r['image']).stem
        img = cv2.imread(str(out / 'images' / r['image']))
        boxes = draft_boxes(drafts[stem])
        r['n_boxes'] = write_yolo(out / 'labels' / f'{stem}.txt', boxes, img.shape[1], img.shape[0])
        r['reviewed'] = 0
    save_meta(rows, out)
    print(f'reset {len(targets)} frames to unreviewed with their draft boxes: '
          f'{", ".join(r["image"] for r in targets)}')


def cmd_pack(args):
    """Bundle reviewed frames, scripts and the base model for training on another (GPU) machine."""
    import io, tarfile
    out = Path(args.out)
    rows = [r for r in load_meta(out) if r['reviewed'] == '1']
    if not rows:
        sys.exit('No reviewed frames yet. Review with label_review.py before packing.')
    if not Path(args.base_model).is_file():
        sys.exit(f'Base model not found: {args.base_model}')
    here = Path(__file__).resolve().parent

    def add_text(tar, name, text):
        data = text.encode()
        info = tarfile.TarInfo(f'phone_ft/{name}')
        info.size, info.mtime = len(data), int(time.time())
        tar.addfile(info, io.BytesIO(data))

    with tarfile.open(args.bundle, 'w:gz') as tar:
        for script in ('phone_data.py', 'live_phone.py'):
            tar.add(here / script, f'phone_ft/{script}')
        tar.add(args.base_model, f'phone_ft/{Path(args.base_model).name}')
        add_text(tar, 'requirements-gpu.txt', REMOTE_REQUIREMENTS)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=META_FIELDS)
        w.writeheader()
        w.writerows(rows)
        add_text(tar, 'phone_dataset/meta.csv', buf.getvalue())
        for r in rows:
            stem = Path(r['image']).stem
            tar.add(out / 'images' / r['image'], f'phone_ft/phone_dataset/images/{r["image"]}')
            tar.add(out / 'labels' / f'{stem}.txt', f'phone_ft/phone_dataset/labels/{stem}.txt')
    counts = {s: sum(r['split'] == s for r in rows) for s in ('train', 'val', 'test')}
    size = Path(args.bundle).stat().st_size / 1e6
    print(f'packed {len(rows)} reviewed frames {counts} + scripts + {args.base_model} -> {args.bundle} ({size:.0f} MB)')
    print('Contains images of real people: copy only to machines you are allowed to use for this data.')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', default=str(OUT))
    p.add_argument('--videos', default=str(VIDEO_DIR))
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('contact-sheet')
    s = sub.add_parser('prelabel')
    s.add_argument('--model', default=PRELABEL_MODEL)
    s = sub.add_parser('build')
    s.add_argument('--budget', type=int, default=700)
    s.add_argument('--max-hard-negatives', type=int, default=120)
    s.add_argument('--max-dark', type=int, default=150)
    s.add_argument('--test-drivers', nargs='*')
    s.add_argument('--val-drivers', nargs='*')
    s.add_argument('--only-new-clips', action='store_true',
                   help='select frames only from clips that have no frames in the dataset yet')
    sub.add_parser('status')
    s = sub.add_parser('darken')
    s.add_argument('--copies', type=int, default=2)
    s.add_argument('--seed', type=int, default=0)
    s = sub.add_parser('train')
    s.add_argument('--model', default='yolo11m.pt')
    s.add_argument('--epochs', type=int, default=60)
    s.add_argument('--batch', type=int, default=16)
    s.add_argument('--freeze', type=int, default=10, help='freeze the first N layers (backbone) to limit overfitting')
    s.add_argument('--device', default=None)
    s.add_argument('--name', default='phone_ft')
    s.add_argument('--min-train', type=int, default=100)
    s.add_argument('--workers', type=int, default=2, help='image-loading processes; use 4-8 on a GPU machine')
    s = sub.add_parser('eval')
    s.add_argument('--model', default=None, help='fine-tuned weights')
    s.add_argument('--baseline', default='yolo11m.pt')
    s.add_argument('--split', default='test')
    s.add_argument('--conf', type=float, default=0.15)
    s = sub.add_parser('pack')
    s.add_argument('--bundle', default='phone_train_bundle.tar.gz')
    s.add_argument('--base-model', default='yolo11m.pt')
    s = sub.add_parser('reset', help='undo review of frames (restores draft boxes)')
    s.add_argument('--all-reviewed', action='store_true')
    s.add_argument('--images', nargs='*', help='image names, e.g. 05_23_62_005853_0005')
    args = p.parse_args()
    dict(contact_sheet=cmd_contact_sheet, prelabel=cmd_prelabel, build=cmd_build, status=cmd_status,
         darken=cmd_darken, train=cmd_train, eval=cmd_eval, pack=cmd_pack,
         reset=cmd_reset)[args.cmd.replace('-', '_')](args)


if __name__ == '__main__':
    main()
