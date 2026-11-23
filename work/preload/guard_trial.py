"""Bounded Yakumo trial for the code guard: boot log + perf sample, guard on or off."""
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'outils/atelier-mhfu'))
import atelier_core as core
import atelier_retro as retro

variant = sys.argv[1]            # guard | noguard
quest = int(sys.argv[2]) if len(sys.argv) > 2 else None
frames = 450                     # 15 s of game frames
env = {} if variant.startswith('guard') else {'PSPRECOMP_NO_CODE_GUARD': '1'}
if variant.endswith('trace'): env['MHP3RD_TRACE_SAVEDATA'] = '1'
core.VARIANTS['normal'] = {'label': f'Code guard {variant}', 'env': env}
repo = PROJECT / 'psprecomp-mhfu'
git = lambda: subprocess.run(['git', '-C', str(repo), 'status', '--short'], capture_output=True, text=True).stdout
status = git()

seed, from_run, expected = retro.resolve_start(start='apres-intro')
name = f'codeguard-{variant}' + (f'-q{quest}' if quest else '')
trial = retro.Trial(core.Workshop(PROJECT), seed, name, 240 if quest else 150, bool(quest), speed=2,
                    from_run=from_run, expected_source=expected)
trial.guard()
result = None
try:
    trial.launch()
    trial.step({'op': 'snapshot', 'name': 'before'})
    trial.step({'op': 'assert_snapshot', 'snapshot': 'before', 'field': 'money', 'equals': 1500})
    if quest:
        trial.pilot.depart(quest, timeout=30, attempts=2)
        trial.pilot.wait_quest(quest, 60)
        time.sleep(5)
    trial.session.inputs(['0:shot bench-start', f'{frames}:shot bench-end'])
    log = Path(trial.trial_dir) / 'stdout.txt'
    deadline = time.monotonic() + 60
    while b'shot bench-end' not in log.read_bytes():
        trial.rp.check()
        if time.monotonic() > deadline: raise TimeoutError('bench window did not finish')
        time.sleep(0.5)
    trial.step({'op': 'snapshot', 'name': 'after'})
    trial.result['state'] = trial.verdict()
except BaseException as exc:
    trial.result.update(state='failed', error=str(exc))
finally:
    result = trial.close()

run_dir = result.get('directory') or trial.trial_dir
if run_dir is None:
    print(json.dumps({k: result.get(k) for k in ('state', 'error', 'run_id', 'protected_unchanged')}, indent=1))
    sys.exit(1)
result['directory'] = str(run_dir)
text = (Path(run_dir) / 'stdout.txt').read_text(encoding='utf-8', errors='replace')
guard_lines = [l for l in text.splitlines() if l.startswith('[code-guard]')]
window = text.split('shot bench-start', 1)[-1].split('shot bench-end', 1)[0]
perf = re.findall(r'\[perf\] fps ([\d.]+) game ([\d.]+) speed (\d+)% \| frame avg ([\d.]+) max [\d.]+ ms \| guest ([\d.]+)',
                  window)
summary = {
    'variant': variant, 'quest': quest, 'state': result.get('state'), 'error': result.get('error'),
    'directory': result.get('directory'), 'protected_unchanged': result.get('protected_unchanged'),
    'remaining_yakumo_pids': result.get('remaining_yakumo_pids'),
    'git_status_unchanged': status == git(), 'code_guard': guard_lines,
    'perf_samples': len(perf),
}
if perf:
    cols = list(zip(*[[float(v) for v in row] for row in perf]))
    for key, values in zip(('fps', 'game', 'speed', 'frame_ms', 'guest_ms'), cols):
        summary[key + '_median'] = statistics.median(values)
        summary[key + '_mean'] = round(statistics.fmean(values), 2)
print(json.dumps(summary, indent=1))
