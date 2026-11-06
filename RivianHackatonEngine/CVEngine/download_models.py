"""Downloads the two YOLO model files next to this script. Resumes if the connection drops, retries many times.
Usage: python3 download_models.py
"""
import os, sys, time, requests

HERE = os.path.dirname(os.path.abspath(__file__))
GH = 'https://github.com/ultralytics/assets/releases/download/v8.3.0/'
# (file name, expected size in bytes, urls to try in order)
FILES = [
    ('yolo11m-pose.pt', 42459307, [GH + 'yolo11m-pose.pt']),
    ('yolo11m.pt',      40684120, [GH + 'yolo11m.pt']),
]

def download(name, size, urls, tries=40):
    path = os.path.join(HERE, name)
    for attempt in range(tries):
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if have == size:
            print('OK', name); return True
        if have > size:
            os.remove(path); have = 0
        url = urls[attempt % len(urls)]
        try:
            r = requests.get(url, headers={'Range': 'bytes=%d-' % have}, stream=True, timeout=30, allow_redirects=True)
            if r.status_code not in (200, 206):
                raise RuntimeError('HTTP %d' % r.status_code)
            if r.status_code == 200 and have:      # server ignored Range -> start over
                have = 0
            with open(path, 'ab' if have else 'wb') as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
                    have += len(chunk)
                    print('\r%s %.1f / %.1f MB' % (name, have/1e6, size/1e6), end='', flush=True)
            print()
        except Exception as e:
            print('\nconnection problem (%s), retrying %d/%d ...' % (str(e)[:80], attempt + 1, tries))
            time.sleep(3)
    return os.path.exists(path) and os.path.getsize(path) == size

ok = all([download(*f) for f in FILES])
print('ALL DONE' if ok else 'FAILED - try another network (phone hotspot) or download the files in a browser')
sys.exit(0 if ok else 1)
