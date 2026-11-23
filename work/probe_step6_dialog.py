"""Bounded two-quest dialog check on private saves; no gameplay success claim."""
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_core as core
import atelier_pilot as pilot


def main():
    seed = '20261020-153034-ceda7b'
    roots = [ROOT / 'game-mhfu/ms0', ROOT / 'atelier-psprecomp/seeds' / seed / 'ms0']
    if not all(path.is_dir() for path in roots):
        raise RuntimeError('original save or seed missing')
    out = ROOT / 'work/step6-dialog-checks.json'
    if out.exists():
        raise RuntimeError('refusing to overwrite prior checks')
    before = {str(path.relative_to(ROOT)): core._tree_digest(path) for path in roots}
    workshop = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(240, cancel.set)
    timer.daemon = True
    result = {'quests': [1, 1213], 'max_seconds': 240, 'before': before}
    began = time.monotonic()
    timer.start()
    try:
        report = pilot.run_campaign(workshop, [1, 1213], seed_id=seed,
                                    rtc_us=1609459200123456, speed=8,
                                    captures='fast', look_seconds=3,
                                    force=True, cancel=cancel,
                                    log=lambda message: print(message, flush=True))
        result['campaign'] = str(report)
    finally:
        timer.cancel()
        workshop.stop()
        result['seconds'] = round(time.monotonic() - began, 2)
        result['deadline_reached'] = cancel.is_set()
        result['after'] = {str(path.relative_to(ROOT)): core._tree_digest(path) for path in roots}
        result['unchanged'] = result['before'] == result['after']
        result['active'] = workshop.status()['active']
        core._write_json(out, result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
