"""Check record/replay: a scripted run records its controller samples, the
recording is replayed headless, and every common frame plus the stop must match.
usage: test_replay.py <fixture-run> <name>"""
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from play_native import ROOT, prepare, run, finish
from PIL import Image, ImageChops

source, name = ROOT/'builds'/sys.argv[1], sys.argv[2]
a, b = ROOT/'builds'/f'{name}-record', ROOT/'builds'/f'{name}-replay'
epoch = '1792000000000000'
prepare(a, source, ROOT/'builds'/'checkpoint14-saved-character'/'ms0')
shutil.copytree(a/'ms0', a/'ms0-start')
code_a = run(a, ['--frame-dump', '60', '--dispatches', '250000'],
             {'MHFU_LIBC_EPOCH_US': epoch, 'MHFU_INPUT': (ROOT/'src'/'input_load_hunter.txt').read_text(),
              'MHFU_INPUT_RECORD': str(a/'input-record.txt')})
ra = finish(a, code_a, dict(epoch_us=epoch))
prepare(b, a, a/'ms0-start')
code_b = run(b, ['--frame-dump', '60'], {'MHFU_LIBC_EPOCH_US': epoch, 'MHFU_INPUT_REPLAY': str(a/'input-record.txt')})
rb = finish(b, code_b, dict(epoch_us=epoch))
frames = {f.name.split('_')[0]: f for f in (b/'frames').glob('*.bmp')}
same = diff = 0
for f in sorted((a/'frames').glob('*.bmp')):
    g = frames.get(f.name.split('_')[0])
    if g is None: continue
    if ImageChops.difference(Image.open(f).convert('RGB'), Image.open(g).convert('RGB')).getbbox() is None: same += 1
    else: diff += 1
print(dict(recorded_samples=sum(1 for _ in (a/'input-record.txt').open()), frames_same=same, frames_diff=diff,
           record_stop=(ra['stop'] or {}).get('reason'), replay_stop=(rb['stop'] or {}).get('reason')))
