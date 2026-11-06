"""Runs the whole pipeline on one video: wheel zone -> pose/phone extraction -> rules -> annotated video.
Usage: python3 run_all.py my_video.mp4 [out_dir]
- If out_dir/wheel.json already exists it is reused; delete it to recalibrate for a new camera setup.
- Rules and rendering require this calibration. Re-run rules after changing it.
- Results (events.csv, frame_flags.csv, annotated.mp4, key_*.jpg) end up in out_dir.
"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2: sys.exit(__doc__)
VIDEO = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else 'out'
os.makedirs(OUT, exist_ok=True)
WHEEL, CACHE = os.path.join(OUT, 'wheel.json'), os.path.join(OUT, 'cache.npz')

def run(script, *args):
    print('\n=== %s ===' % script, flush=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args])
    return r.returncode

if os.path.exists(WHEEL):
    print('Reusing existing', WHEEL)
elif run('auto_wheel.py', VIDEO, WHEEL) != 0:
    sys.exit('\nAutomatic wheel detection failed. Check the errors above and retry, or supply '
             'a validated wheel.json with wheel points and frame_size in %s.' % OUT)
else:
    print('Check %s before trusting the result.' % os.path.join(OUT, 'wheel_preview.jpg'))

for step in (('extract.py', VIDEO, CACHE), ('rules.py', CACHE), ('render.py', VIDEO, CACHE)):
    if run(*step) != 0: sys.exit('Step %s failed, see the message above.' % step[0])
print('\nDONE. Open %s and %s' % (os.path.join(OUT, 'events.csv'), os.path.join(OUT, 'annotated.mp4')))
