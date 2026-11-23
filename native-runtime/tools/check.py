"""One command to check the native port before a run or a play session.

Default: unit and differential tests in parallel (a few minutes).
--scenes: also replays the regression scenes with a fixed clock and compares
every dumped frame and the final RAM hash with builds/regression/<scene>.json.
--update writes those references from the current code (only after a change
whose visible effect was verified on images).
Prints one line per check and exits non-zero on any failure.
--tests selects unit-test groups; --out chooses a new report directory.
results.json records progress and the final outcome in that directory.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from play_native import ZIG, DATA, prepare, run  # noqa: E402

PY = sys.executable
EPOCH = '1792000000000000'
TEST_NAMES = ('object_cache', 'interpreter', 'renderer', 'window', 'delay_variants', 'pad')
SCENES = {
    'hunter-exit-village': dict(fixtures='continuation-exit60', ms0='checkpoint14-saved-character/ms0',
                                input='src/input_regression_exit.txt', dispatches='500000'),
    'creation-village': dict(fixtures='checkpoint15-village', ms0='checkpoint14-saved-character/initial-ms0',
                             input='src/input_character_creation_yes.txt', dispatches='250000'),
}


def unit_tests(base, selected=None):
    src = ROOT/'src'
    tests = {
        'object_cache': [PY, str(src/'test_object_cache.py'), '--zig', str(ZIG), '--out', str(base/'object_cache')],
        'interpreter': [PY, str(src/'test_native_interpreter.py'), '--zig', str(ZIG), '--out', str(base/'interpreter')],
        'renderer': [PY, str(src/'test_native_renderer.py'), '--zig', str(ZIG), '--out', str(base/'renderer')],
        'window': [PY, str(src/'test_native_window.py'), '--zig', str(ZIG), '--out', str(base/'window')],
        'delay_variants': [PY, str(src/'test_overlay_delay_variants.py'), '--data', str(DATA), '--zig', str(ZIG), '--out', str(base/'delay_variants')],
    }
    def pad():
        out = base/'pad'; out.mkdir(parents=True)
        for name in ('native_window.c', 'native_window.h', 'test_native_pad.c'): (out/name).write_bytes((src/name).read_bytes())
        build = subprocess.run([str(ZIG), 'cc', '-O1', '-I.', 'test_native_pad.c', '-luser32', '-lgdi32', '-o', 'pad.exe'], cwd=out, capture_output=True, text=True)
        if build.returncode: return 1, build.stderr[-300:]
        r = subprocess.run([str(out/'pad.exe')], capture_output=True, text=True); return r.returncode, r.stdout.strip()[-200:]
    def command(name):
        r = subprocess.run(tests[name], cwd=src, capture_output=True, text=True)
        return r.returncode, (r.stdout+r.stderr).strip().splitlines()[-1][:200] if (r.stdout+r.stderr).strip() else ''
    jobs = {name: (lambda n=name: command(n)) for name in tests}; jobs['pad'] = pad
    if selected is not None:
        jobs = {name: job for name, job in jobs.items() if name in selected}
    if not jobs:
        return {}
    with concurrent.futures.ThreadPoolExecutor(len(jobs)) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        return {name: future.result() for name, future in futures.items()}


def frame_hashes(out):
    return {f.name.split('_')[0]: hashlib.sha256(f.read_bytes()[54:]).hexdigest()  # pixels only (skip BMP header)
            for f in sorted((out/'frames').glob('*.bmp'))}


def scene(name, spec, base):
    out = base/name
    prepare(out, ROOT/'builds'/spec['fixtures'], ROOT/'builds'/spec['ms0'])
    run(out, ['--frame-dump', '60', '--dispatches', spec['dispatches']],
        {'MHFU_LIBC_EPOCH_US': EPOCH, 'MHFU_INPUT': (ROOT/spec['input']).read_text().strip(),
         # One rasterizer thread: band order is then fixed (see journal, row-0 race).
         'RT_GE_THREADS': '1'})
    text = (out/'play.stdout.jsonl').read_text(encoding='utf-8', errors='replace')
    ram = re.findall(r'"memory_sha256":"(\w+)"', text[-50000:])
    stops = [json.loads(x) for x in (out/'play.stderr.jsonl').read_text(errors='replace').splitlines() if x.startswith('{')]
    (out/'play.stdout.jsonl').unlink()
    return dict(frames=frame_hashes(out), ram=ram[-1] if ram else None, stop=stops[-1].get('reason') if stops else None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenes', action='store_true'); p.add_argument('--update', action='store_true')
    p.add_argument('--only-scenes', action='store_true', help='skip unit tests')
    p.add_argument('--tests', nargs='+', choices=TEST_NAMES, help='unit-test groups to run (default: all)')
    p.add_argument('--out', type=Path, help='new directory for reports and test outputs')
    args = p.parse_args()
    base = args.out.resolve() if args.out is not None else ROOT/'builds'/'checks'/time.strftime('%Y%m%d-%H%M%S')
    base.mkdir(parents=True)
    report = dict(status='running', units={}, scenes={})
    def save_report():
        temporary = base/'results.json.tmp'
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        temporary.replace(base/'results.json')
    save_report()
    failed = False
    try:
        if not args.only_scenes:
            for name, (code, last) in unit_tests(base, args.tests).items():
                report['units'][name] = dict(exit_code=code, summary=last)
                save_report()
                print(f"{'OK ' if not code else 'ECHEC'} {name:15} {last}"); failed |= bool(code)
        if args.scenes or args.update or args.only_scenes:
            references = ROOT/'builds'/'regression'; references.mkdir(exist_ok=True)
            with concurrent.futures.ThreadPoolExecutor(len(SCENES)) as pool:
                results = {name: pool.submit(scene, name, spec, base) for name, spec in SCENES.items()}
                for name, future in results.items():
                    result = future.result(); ref_path = references/f'{name}.json'
                    if args.update:
                        ref_path.write_text(json.dumps(result, indent=0), encoding='utf-8')
                        report['scenes'][name] = dict(ok=True, identical_frames=None, total_frames=len(result['frames']),
                                                    ram_identical=None, stop_identical=None, reference_updated=True)
                        save_report()
                        print(f"REF  {name:22} {len(result['frames'])} images, RAM {str(result['ram'])[:12]}, arret {result['stop']}"); continue
                    if not ref_path.exists():
                        report['scenes'][name] = dict(ok=False, identical_frames=None, total_frames=len(result['frames']),
                                                    ram_identical=None, stop_identical=None, error='missing reference')
                        save_report()
                        print(f'ECHEC {name}: pas de reference (lancer --update)'); failed = True; continue
                    ref = json.loads(ref_path.read_text(encoding='utf-8'))
                    common = [k for k in ref['frames'] if k in result['frames']]
                    diff = [k for k in common if ref['frames'][k] != result['frames'][k]]
                    ok = not diff and len(common) == len(ref['frames']) == len(result['frames']) and ref['ram'] == result['ram'] and ref['stop'] == result['stop']
                    report['scenes'][name] = dict(ok=ok, identical_frames=len(common)-len(diff), total_frames=len(ref['frames']),
                                                ram_identical=ref['ram'] == result['ram'], stop_identical=ref['stop'] == result['stop'])
                    save_report()
                    print(f"{'OK ' if ok else 'ECHEC'} {name:22} images {len(common)-len(diff)}/{len(ref['frames'])} identiques"
                          f"{', premiere difference '+diff[0] if diff else ''}, RAM {'identique' if ref['ram'] == result['ram'] else 'DIFFERENTE'}")
                    failed |= not ok
    except (Exception, KeyboardInterrupt) as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        save_report()
        raise
    report['status'] = 'failed' if failed else 'complete'
    save_report()
    print(f'Dossier : {base}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
