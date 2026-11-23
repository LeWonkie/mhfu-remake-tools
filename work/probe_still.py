"""Bounded check of `hold still`, `monster N here` and `goto monster N` with
the boss in the hunter's area: finds the boss as probe_capture.py does, then
reads its position free, held still and let go. Captures at each step.
Usage: probe_still.py [QUEST] [SECONDS]
"""
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_campaign as campaign
import atelier_core as core
import atelier_pilot as pilot_mod
import atelier_zones as zones
from hunt import first_hop
from zone_tour import next_step


def main():
    quest = int(sys.argv[1]) if len(sys.argv) > 1 else 1020
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 360
    w = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(seconds, cancel.set); timer.daemon = True; timer.start()
    log = {'quest': quest, 'state': 'starting', 'steps': [], 'shots': []}
    s = None

    def step(**event):
        event['t'] = round(time.monotonic() - began, 1)
        log['steps'].append(event)

    def shot(name, frames=5):
        log['shots'] += p.confirm_shots([p.shoot(name, frames)])

    began = time.monotonic()
    try:
        scenario = {'name': 'campagne', 'label': 'Hold still probe', 'debug': True, 'fast_boot': True,
                    'script': campaign.BOOT, 'screenshot_every': 0,
                    'settings': {'video.fast_forward': 'toggle', 'video.fast_forward_speed': '2',
                                 'video.fast_loading': '1'}}
        s = campaign.Session(w, '20261020-153034-ceda7b', seconds + 15,
                             variant=core.register_rtc_variant('normal', 1609459200123456), scenario=scenario)
        s.cancel = cancel
        log['run_id'] = s.run_id
        p = pilot_mod.Pilot(s, cancel=cancel)
        s.arm_loadout('best'); p.wait_village(60)
        p.depart(quest, 60); p.wait_quest(quest, 90)
        s.ask('hold health on', 'health', 5)
        goal = [e for e in zones.mission_objectives(s, quest)['initial'] if e['flags']]
        species = goal[0]['target']
        graph, visited, boss = {}, set(), None
        while not cancel.is_set():
            here = zones.current(s, timeout=4)
            stage = here['stage']
            visited.add(stage)
            graph[stage] = [e['destination'] for e in here['exits'] if zones.usable(e) and e['destination'] != stage]
            found = [m for m in zones.monsters(s) if m['type'] == species and m['health'] > 0]
            local = [m for m in found if m['stage'] == stage]
            if local:
                boss = local[0]
                break
            target = next((first_hop(graph, stage, m['stage']) for m in found if first_hop(graph, stage, m['stage'])), None)
            hop = target or next_step(graph, visited, stage)
            if hop is None:
                raise zones.Unavailable('boss not found')
            exit = next(e for e in here['exits'] if e['destination'] == hop and zones.usable(e))
            zones.traverse(s, here, exit, settle=1)
        slot = boss['slot']

        def track(label, count=12, every=0.5):
            seen = []
            for _ in range(count):
                m = next((m for m in zones.monsters(s) if m['slot'] == slot), None)
                if m:
                    seen.append([round(v) for v in m['position']])
                time.sleep(every)
            step(kind='track', label=label, first=seen[:1], last=seen[-1:],
                 spread=max((abs(a[i] - b[i]) for a in seen for b in seen for i in (0, 2)), default=None))

        step(kind='boss', slot=slot, stage=boss['stage'])
        track('free')
        step(kind='answer', line='hold still on', answer=s.ask('hold still on', 'hold still', 5))
        track('held')
        shot('still-held')
        step(kind='answer', line=f'goto monster {slot}', answer=s.ask(f'goto monster {slot}', 'moved', 5))
        time.sleep(1)
        step(kind='answer', line=f'monster {slot} here', answer=s.ask(f'monster {slot} here', 'moved', 5))
        track('held after here', count=6)
        shot('still-here')
        step(kind='answer', line='hold still off', answer=s.ask('hold still off', 'hold still', 5))
        track('let go')
        log['state'] = 'done'
    except (pilot_mod.Stopped, pilot_mod.WaitTimeout, zones.Unavailable, ValueError, StopIteration) as error:
        log.update(state='cancelled' if cancel.is_set() else 'incomplete', reason=repr(error))
    finally:
        timer.cancel()
        if s:
            core._write_json(s.dir / 'still.json', log)
        w.stop()
    print(json.dumps(log, indent=1), flush=True)


if __name__ == '__main__':
    main()
