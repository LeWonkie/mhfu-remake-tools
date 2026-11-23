"""Read-only coverage audit, including successful rows of resumed surveys."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_plan
import atelier_pilot


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan = atelier_plan.load_plan(ROOT)
    covered, evidence, failures = {}, [], []

    def add_rows(path, campaign, approved, kind):
        accepted, missing = [], []
        rows = {row['quest']: row for row in campaign['results']}
        for quest in approved:
            row = rows.get(quest)
            if not row or not (row['started'] and row['reached'] and row['returned'] and not row['crashed']):
                raise ValueError(f'Inconsistent approved quest {quest}: {path}')
            frames = ROOT / 'atelier-psprecomp/runs' / row['run_id'] / 'frames'
            expected = [frames / f'q{quest}-{phase}.bmp' for phase in ('arrivee', 'pendant', 'fin', 'fin2', 'village')]
            absent = [str(p.relative_to(ROOT)) for p in expected if not p.is_file()]
            if absent:
                missing.extend(absent)
                continue
            covered[quest] = {'quest': quest, 'outcome': row['outcome'], 'mission_state': row.get('mission_state'),
                              'source': str(path.relative_to(ROOT)), 'run_id': row['run_id']}
            accepted.append(quest)
        evidence.append({'source': str(path.relative_to(ROOT)), 'kind': kind, 'accepted': accepted, 'missing': missing})
        failures.extend({'quest': row['quest'], 'outcome': row['outcome'], 'source': str(path.relative_to(ROOT))}
                        for row in campaign['results'] if row['quest'] not in approved)

    paths = sorted(set((ROOT / 'work').glob('step6-batch*/checks.json')) |
                   set((ROOT / 'work').glob('step6-check*/checks.json')))
    baseline = None
    for path in paths:
        check = read(path)
        if not (check.get('state') == 'completed' and check.get('unchanged') is True
                and not check.get('active') and not check.get('deadline_reached')):
            continue
        if check['before'] != check['after']:
            raise ValueError(f'Incorrect unchanged claim: {path}')
        if baseline is None:
            baseline = check['before']
        if check['before'] != baseline:
            raise ValueError(f'Originals differ across archives: {path}')
        campaign = read(Path(check['campaign']) / 'campagne.json')
        add_rows(path, campaign, check['survey_passed'], 'completed_campaign_rows')

    for path in sorted((ROOT / 'work').glob('step6-batch*/interruption.json')):
        interrupted = read(path)
        if not (interrupted.get('unchanged_since_previous_completed_batch') is True
                and interrupted.get('runtime_active_observed') is False
                and interrupted.get('originals_sha256') == baseline):
            continue
        campaign = read(ROOT / interrupted['campaign'])
        approved = interrupted['completed_quests']
        for row in campaign['results']:
            if row['quest'] in approved and not atelier_pilot.survey_passed(type('Result', (), row)()):
                raise ValueError(f'Interrupted row does not pass current technical criteria: {path}')
        add_rows(path, campaign, approved, 'interrupted_completed_rows_exit_code_unknown')

    eligible = set(plan['quests'])
    observed = eligible & covered.keys()
    remaining = sorted(eligible - observed)
    batches = [{'batch': i, 'covered': len(set(batch) & observed), 'total': len(batch),
                'remaining': sorted(set(batch) - observed)} for i, batch in enumerate(plan['batches'], 1)]
    result = {'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
              'scope': 'Technical coverage from archived rows; no normal-gameplay or current-cache validation. '
                       'Interrupted campaigns retain unknown exit status; coverage is a union, not a clean full run.',
              'eligible': len(eligible), 'covered_distinct': len(observed), 'remaining': remaining,
              'batches': batches, 'outcomes_as_recorded': dict(Counter(covered[q]['outcome'] for q in observed)),
              'evidence': evidence, 'failed_attempts_retained': failures,
              'quests': [covered[q] for q in sorted(observed)]}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'status.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# Couverture technique cumulée MHFU', '',
             f'{len(observed)}/{len(eligible)} quêtes distinctes ; {len(remaining)} restantes.', '',
             result['scope'], '', '| Lot | Couverture | Restantes |', '| --- | --- | --- |']
    lines.extend(f"| {b['batch']} | {b['covered']}/{b['total']} | {', '.join(map(str, b['remaining']))} |" for b in batches)
    lines += ['', '## Sources', '']
    lines.extend(f"- `{e['source']}` : {len(e['accepted'])} résultats retenus ({e['kind']})." for e in evidence)
    (args.out / 'status.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'covered': len(observed), 'eligible': len(eligible), 'remaining': len(remaining),
                      'failed_attempts': len(failures), 'out': str(args.out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
