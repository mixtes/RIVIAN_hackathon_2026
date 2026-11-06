"""Downloads the two files needed for automatic wheel detection (about 600 MB total). Resumes if the connection drops.
Usage: python3 download_wheel_models.py
"""
import os, sys, time, requests

HERE = os.path.dirname(os.path.abspath(__file__))
GH = 'https://github.com/ultralytics/assets/releases/download/v8.3.0/'
FILES = [('yoloe-11m-seg.pt', GH + 'yoloe-11m-seg.pt'), ('mobileclip_blt.ts', GH + 'mobileclip_blt.ts')]

def total_size(url):
    r = requests.get(url, headers={'Range': 'bytes=0-0'}, timeout=30, allow_redirects=True)
    cr = r.headers.get('Content-Range', '')           # e.g. "bytes 0-0/12345"
    return int(cr.split('/')[-1]) if '/' in cr else int(r.headers.get('Content-Length', 0))

def download(name, url, tries=60):
    path = os.path.join(HERE, name)
    size = None
    for attempt in range(tries):
        try:
            if size is None: size = total_size(url)
            have = os.path.getsize(path) if os.path.exists(path) else 0
            if have == size and size > 0:
                print('OK', name); return True
            if have > size: os.remove(path); have = 0
            r = requests.get(url, headers={'Range': 'bytes=%d-' % have}, stream=True, timeout=30, allow_redirects=True)
            if r.status_code not in (200, 206): raise RuntimeError('HTTP %d' % r.status_code)
            if r.status_code == 200: have = 0           # server ignored Range -> start over
            with open(path, 'ab' if have else 'wb') as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk); have += len(chunk)
                    print('\r%s %.1f / %.1f MB' % (name, have / 1e6, size / 1e6), end='', flush=True)
            print()
        except Exception as e:
            print('\nconnection problem (%s), retrying %d/%d ...' % (str(e)[:80], attempt + 1, tries)); time.sleep(3)
    return os.path.exists(path) and size is not None and os.path.getsize(path) == size

ok = all([download(*f) for f in FILES])
print('ALL DONE' if ok else 'FAILED - try another network (phone hotspot)')
sys.exit(0 if ok else 1)
