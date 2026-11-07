"""Review and correct phone boxes for the fine-tuning dataset (local OpenCV window).

Usage: python label_review.py [--out phone_dataset] [--filter all|unreviewed|phone|dark|negatives]
                              [--start-at FRAME_OR_CLIP] [--zoom 0.8]

Draft boxes come from the pre-labeling model and are often wrong; every frame you
confirm becomes ground truth. Box exactly the phone, also when it is partly hidden
by fingers. A frame with no phone is confirmed with no boxes.

Mouse: drag to draw a box, click a box to select it, right-click a box to delete it.
Keys:  SPACE / ENTER  confirm frame as shown and go to next
       d, DEL, BACKSPACE  delete the selected box, else the box under the mouse, else the only box
       c              clear all boxes (no phone)
       s              skip without saving        b  back to previous frame
       u              next unreviewed frame      l  toggle brightened view (display only)
       + / -          zoom the window in / out   q or ESC  quit (confirmed frames are already saved)
Keys work with Caps Lock on. The window fits your screen automatically; --zoom 0.8 starts it smaller.
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

import phone_data as pd

MIN_BOX = 6
SCREEN_FILL_W, SCREEN_FILL_H = 0.9, 0.8     # leave room for the window title bar and taskbar
HELP = ('drag=box  right-click=delete box  d=delete  c=clear  SPACE=confirm  s=skip  b=back  '
        'u=unreviewed  l=light  +/-=zoom  q=quit')
DELETE = -2                                 # normalized code for every "delete box" key
DELETE_KEYS = {ord('d'), 8, 0x2E0000, 0xFFFF, 0xFF9F}   # d, Backspace, Delete (Windows, GTK, X11 keypad)


def read_key(code):
    """waitKeyEx code -> lower-case character code, DELETE, or -1. Caps Lock does not matter."""
    if code == -1:
        return -1
    if code in DELETE_KEYS:
        return DELETE
    if code > 0xFF:
        code &= 0xFF
        if not code:
            return -1                           # other special keys (arrows, F-keys) are ignored
    if ord('A') <= code <= ord('Z'):
        code += 32
    return DELETE if code == ord('d') else code


def screen_size():
    """Usable screen size in the same pixels the OpenCV window uses; a safe default elsewhere."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1280, 800


def fit_scale(img_w, img_h, screen_w, screen_h, bar_h, zoom=1.0):
    """Scale so the image plus the status bar fits on screen (never enlarged before zoom)."""
    fit = min(SCREEN_FILL_W * screen_w / img_w, (SCREEN_FILL_H * screen_h - bar_h) / img_h, 1.0)
    return max(0.1, fit * zoom)


def status_bar(width, lines):
    """Black bar with text sized to fit the given width; returns (bar, height)."""
    widest = max(cv2.getTextSize(t, 0, 1.0, 1)[0][0] for t, _ in lines)
    font = max(0.3, min(0.6, (width - 16) / widest))
    line_h = int(30 * font / 0.6) + 4
    bar = np.zeros((line_h * len(lines) + 8, width, 3), np.uint8)
    for i, (text, color) in enumerate(lines):
        cv2.putText(bar, text, (8, line_h * (i + 1)), 0, font, color, 1, cv2.LINE_AA)
    return bar, bar.shape[0]


class Session:
    """Review state without any GUI, so it can be tested."""

    def __init__(self, out, rows):
        self.out, self.rows, self.all_rows = Path(out), rows, None
        self.index, self.boxes, self.selected, self.image = 0, [], None, None

    def load(self, index):
        self.index = max(0, min(index, len(self.rows) - 1))
        row = self.rows[self.index]
        self.image = cv2.imread(str(self.out / 'images' / row['image']))
        if self.image is None:
            raise FileNotFoundError(self.out / 'images' / row['image'])
        h, w = self.image.shape[:2]
        self.boxes = pd.read_yolo(self.label_path(row), w, h)
        self.selected = None
        return row

    def label_path(self, row):
        return self.out / 'labels' / (Path(row['image']).stem + '.txt')

    def add_box(self, x1, y1, x2, y2):
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x2 - x1 < MIN_BOX or y2 - y1 < MIN_BOX:
            return False
        self.boxes.append((x1, y1, x2, y2))
        self.selected = len(self.boxes) - 1
        return True

    def select_at(self, x, y):
        hits = [i for i, (x1, y1, x2, y2) in enumerate(self.boxes) if x1 <= x <= x2 and y1 <= y <= y2]
        self.selected = min(hits, key=lambda i: (self.boxes[i][2] - self.boxes[i][0]) *
                            (self.boxes[i][3] - self.boxes[i][1])) if hits else None
        return self.selected is not None

    def delete_selected(self):
        if self.selected is not None:
            del self.boxes[self.selected]
            self.selected = None
            return True
        return False

    def delete(self, x=None, y=None):
        """Delete the selected box; otherwise the box under (x, y); otherwise the only box."""
        if self.selected is None and x is not None:
            self.select_at(x, y)
        if self.selected is None and len(self.boxes) == 1:
            self.selected = 0
        return self.delete_selected()

    def clear(self):
        self.boxes, self.selected = [], None

    def confirm(self):
        """Save boxes as ground truth and mark the frame reviewed in meta.csv."""
        row = self.rows[self.index]
        h, w = self.image.shape[:2]
        n = pd.write_yolo(self.label_path(row), self.boxes, w, h)
        meta = pd.load_meta(self.out)
        for r in meta:
            if r['image'] == row['image']:
                r['reviewed'], r['n_boxes'] = 1, n
        pd.save_meta(meta, self.out)
        row['reviewed'], row['n_boxes'] = '1', str(n)
        return n

    def next_unreviewed(self, start):
        for i in list(range(start, len(self.rows))) + list(range(0, start)):
            if self.rows[i]['reviewed'] != '1':
                return i
        return None


def filter_rows(rows, name):
    keep = dict(all=lambda r: True, unreviewed=lambda r: r['reviewed'] != '1',
                phone=lambda r: int(r['n_boxes']) > 0, dark=lambda r: r['dark_clip'] == '1',
                negatives=lambda r: int(r['n_boxes']) == 0)[name]
    return [r for r in rows if keep(r)]


def find_start(rows, name):
    """Index of the first frame whose name starts with `name` (with or without .jpg), else None."""
    name = name[:-4] if name.endswith('.jpg') else name
    return next((i for i, r in enumerate(rows) if r['image'].startswith(name)), None)


def brighten(img):
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(l)
    out = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
    return cv2.convertScaleAbs(out, alpha=1.6, beta=15)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', default=str(pd.OUT))
    p.add_argument('--filter', default='all', choices=['all', 'unreviewed', 'phone', 'dark', 'negatives'])
    p.add_argument('--zoom', type=float, default=1.0, help='window size relative to fit-to-screen, e.g. 0.8')
    p.add_argument('--start-at', default=None,
                   help='open at this frame, e.g. 05_23_62_020922_0645 (or just a clip name like 05_23_62_020922)')
    args = p.parse_args()
    rows = filter_rows(pd.load_meta(args.out), args.filter)
    if not rows:
        raise SystemExit('No frames to review. Run "python phone_data.py build" first.')
    s = Session(args.out, rows)
    if args.start_at:
        start = find_start(rows, args.start_at)
        if start is None:
            raise SystemExit(f'No frame starting with {args.start_at} in this view (filter "{args.filter}").')
    else:
        start = s.next_unreviewed(0)
    row = s.load(start if start is not None else 0)
    screen_w, screen_h = screen_size()
    state = dict(drag=None, bright=None, scale=1.0, bar_h=70, zoom=args.zoom, mouse=None)
    window = 'phone review'

    def to_img(x, y):
        return x / state['scale'], (y - state['bar_h']) / state['scale']

    def on_mouse(event, x, y, flags, _):
        ix, iy = to_img(x, y)
        state['mouse'] = (ix, iy)
        if event == cv2.EVENT_LBUTTONDOWN:
            if not s.select_at(ix, iy):
                state['drag'] = (ix, iy, ix, iy)
        elif event == cv2.EVENT_RBUTTONDOWN:
            s.selected = None
            s.delete(ix, iy)
        elif event == cv2.EVENT_MOUSEMOVE and state['drag']:
            state['drag'] = state['drag'][:2] + (ix, iy)
        elif event == cv2.EVENT_LBUTTONUP and state['drag']:
            s.add_box(*state['drag'])
            state['drag'] = None

    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window, on_mouse)
    while True:
        h, w = s.image.shape[:2]
        dark = row['dark_clip'] == '1'
        show_bright = state['bright'] if state['bright'] is not None else dark
        view = brighten(s.image) if show_bright else s.image.copy()
        for i, (x1, y1, x2, y2) in enumerate(s.boxes):
            cv2.rectangle(view, (int(x1), int(y1)), (int(x2), int(y2)),
                          (0, 255, 255) if i == s.selected else (255, 0, 255), 3)
        if state['drag']:
            x1, y1, x2, y2 = state['drag']
            cv2.rectangle(view, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
        state['scale'] = fit_scale(w, h, screen_w, screen_h, state['bar_h'], state['zoom'])
        view = cv2.resize(view, (int(w * state['scale']), int(h * state['scale'])))
        done = sum(r['reviewed'] == '1' for r in s.rows)
        status = 'CONFIRMED' if row['reviewed'] == '1' else 'draft'
        bar, state['bar_h'] = status_bar(view.shape[1], [
            (f'{s.index + 1}/{len(s.rows)}  {row["image"]}  [{row["split"]}, {row["reason"]}, '
             f'pre-label {float(row["prelabel_conf"]):.2f}]  {status}  boxes: {len(s.boxes)}', (255, 255, 255)),
            (f'reviewed {done}/{len(s.rows)}{"  light ON" if show_bright else ""}', (180, 220, 255)),
            (HELP, (180, 220, 255))])
        cv2.imshow(window, np.vstack([bar, view]))
        key = read_key(cv2.waitKeyEx(30))
        if key in (ord('q'), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            break
        if key in (ord(' '), 13):
            s.confirm()
            nxt = s.index + 1
            if nxt >= len(s.rows):
                nxt = s.next_unreviewed(0)
                if nxt is None:
                    print('All frames reviewed.')
                    break
            row = s.load(nxt)
            state['bright'] = None
        elif key == DELETE:
            s.delete(*(state['mouse'] or (None, None)))
        elif key == ord('c'):
            s.clear()
        elif key == ord('s'):
            row = s.load(s.index + 1)
            state['bright'] = None
        elif key == ord('b'):
            row = s.load(s.index - 1)
            state['bright'] = None
        elif key == ord('u'):
            nxt = s.next_unreviewed(s.index + 1)
            if nxt is not None:
                row = s.load(nxt)
                state['bright'] = None
        elif key == ord('l'):
            state['bright'] = not show_bright
        elif key in (ord('+'), ord('=')):
            state['zoom'] = min(3.0, state['zoom'] * 1.15)
        elif key in (ord('-'), ord('_')):
            state['zoom'] = max(0.3, state['zoom'] / 1.15)
    cv2.destroyAllWindows()
    print(f'reviewed {sum(r["reviewed"] == "1" for r in s.rows)}/{len(s.rows)} frames in this view')


if __name__ == '__main__':
    main()
