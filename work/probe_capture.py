"""Bounded capture attempt on a hunt quest: find the boss, weaken it with
`monsterhp`, set a Shock Trap in front of the hunter, move the boss onto it,
throw Tranq Bombs, and watch the mission state. Captures at each step.
Usage: probe_capture.py QUEST [SECONDS]
"""
import json
import math
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
from hunt import first_hop, placement
from zone_tour import next_step

SHOCK_TRAP, TRANQ_BOMB, MEGA_POTION = 79, 74, 8


def main():
    quest = int(sys.argv[1])
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 420
    w = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(seconds, cancel.set); timer.daemon = True; timer.start()
    log = {'quest': quest, 'state': 'starting', 'steps': [], 'shots': []}
    s = None

    def step(**event):
        event['t'] = round(time.monotonic() - began, 1)
        log['steps'].append(event)
        print('STEP', json.dumps(event), flush=True)

    def shot(name, frames=10):
        log['shots'] += p.confirm_shots([p.shoot(name, frames)])

    began = time.monotonic()
    try:
        scenario = {'name': 'campagne', 'label': 'Capture probe', 'debug': True, 'fast_boot': True,
                    'dump_overlays': True, 'script': campaign.BOOT, 'screenshot_every': 0,
                    'settings': {'video.fast_forward': 'toggle', 'video.fast_forward_speed': '2',
                                 'video.fast_loading': '1'}}
        s = campaign.Session(w, '20261020-153034-ceda7b', seconds + 15,
                             variant=core.register_rtc_variant('normal', 1609459200123456), scenario=scenario)
        s.cancel = cancel
        log['run_id'] = s.run_id
        p = pilot_mod.Pilot(s, cancel=cancel)
        s.arm_loadout('best'); p.wait_village(60)
        shot('cap-village', 60)
        p.depart(quest, 60); p.wait_quest(quest, 90)
        s.ask('hold health on', 'health', 5)
        goal = [e for e in zones.mission_objectives(s, quest)['initial'] if e['flags']]
        species = goal[0]['target']
        step(kind='objective', goal=goal)

        # Find the boss: explore areas until it is in the hunter's.
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
            result = zones.traverse(s, here, exit, settle=1)
            step(kind='hop', frm=stage, to=hop, reached=result['reached'], boss_areas=[m['stage'] for m in found])
        def settled(read, what, timeout=12):
            """The height once it stops changing (a flying boss lands, a placed hunter falls)."""
            last, deadline = read()[1], time.monotonic() + timeout
            while time.monotonic() < deadline:
                time.sleep(0.5)
                now = read()[1]
                if abs(now - last) < 3:
                    return now
                last = now
            step(kind='not_settled', what=what)
            return last

        settled(lambda: next(m for m in zones.monsters(s) if m['slot'] == boss['slot'])['position'], 'boss')
        boss = next(m for m in zones.monsters(s) if m['slot'] == boss['slot'])
        step(kind='boss', stage=boss['stage'], health=boss['health'], position=boss['position'])
        shot('cap-boss-found', 5)

        # A Shock Trap in the first pouch slot (the item bar's selection).
        for line in (f'pouch {MEGA_POTION} 0', f'pouch {SHOCK_TRAP} 0', f'pouch {SHOCK_TRAP} 1'):
            step(kind='pouch', line=line, answer=s.ask(line, 'pouch', 5))

        # The hunter beside the boss; a short walk gives its facing.
        here = zones.current(s, timeout=4)
        spot = placement(boss['position'], here['exits']) or boss['position']
        zones.place(s, here['player'], (spot[0] + 400, spot[1] + 30, spot[2]))
        settled(lambda: zones.current(s)['position'], 'hunter')
        before = zones.current(s)['position']
        s.inputs(['0:axis lefty -1', '10:axis lefty 0'])
        time.sleep(1.5)
        after = zones.current(s)['position']
        dx, dz = after[0] - before[0], after[2] - before[2]
        norm = math.hypot(dx, dz) or 1.0
        facing = (dx / norm, dz / norm)
        step(kind='facing', before=before, after=after, facing=facing)

        # The trap, until it leaves the pouch.
        settled(lambda: zones.current(s)['position'], 'hunter')
        for attempt in range(3):
            s.inputs(['0:pad x 6'])
            time.sleep(2.5)
            answer = s.ask('pouch', 'pouch:', 5) or ''
            step(kind='trap_press', attempt=attempt, pouch=answer)
            if f' {SHOCK_TRAP}x' not in answer:
                break
        shot('cap-trap-set', 5)
        hunter = zones.current(s)['position']
        trap = (hunter[0] + facing[0] * 40, hunter[1], hunter[2] + facing[1] * 40)
        zones.place(s, here['player'], (hunter[0] - facing[0] * 300, hunter[1], hunter[2] - facing[1] * 300))
        for _ in range(4):
            zones.place(s, boss['address'], trap)
            time.sleep(0.25)
        time.sleep(1)
        shot('cap-boss-on-trap', 1)
        # Weakened only once held: a weak boss flies off (run 20261021-174549-92daaa).
        weak = max(1, boss['health'] // 5)
        step(kind='weaken', answer=s.ask(f'monsterhp {weak}', 'monsters set', 5))
        step(kind='trap', hunter=hunter, trap=trap, pouch=s.ask('pouch', 'pouch:', 5),
             boss=next((m for m in zones.monsters(s) if m['slot'] == boss['slot']), None))

        # Tranq Bombs, first pouch slot.
        for line in (f'pouch {SHOCK_TRAP} 0', f'pouch {TRANQ_BOMB} 0', f'pouch {TRANQ_BOMB} 10'):
            step(kind='pouch', line=line, answer=s.ask(line, 'pouch', 5))
        for throw in range(3):
            s.inputs(['0:pad x 6'])
            time.sleep(1.6)
            mission = zones.mission_state(s)
            alive = [m for m in zones.monsters(s) if m['slot'] == boss['slot']]
            step(kind='tranq', throw=throw, mission=mission, boss=alive[0] if alive else None)
            shot(f'cap-tranq-{throw}', 5)
            if zones.mission_over(mission):
                break
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not zones.mission_over(zones.mission_state(s)):
            time.sleep(1)
        log['mission'] = zones.mission_state(s)
        log['objective_after'] = zones.mission_objectives(s, quest)
        shot('cap-end', 5)
        if zones.mission_over(log['mission']):
            def back():
                snap = p.snapshot()
                if snap['task'] == pilot_mod.VILLAGE_TASK:
                    return snap
                p.ask(['quest results'], ['results countdown'], 1)
                s.inputs(['0:pad b 6'])
            p.until('village', back, 120, poll=0.3)
            p.wait_village(30)
            log['village'] = True
            shot('cap-village-after', 60)
        log['state'] = 'done'
    except (pilot_mod.Stopped, pilot_mod.WaitTimeout, zones.Unavailable, ValueError, StopIteration) as error:
        log.update(state='cancelled' if cancel.is_set() else 'incomplete', reason=repr(error))
    finally:
        timer.cancel()
        if s:
            core._write_json(s.dir / 'capture.json', log)
        core._write_json(ROOT / 'work/step5-probe' / f'capture-{quest}.json', log)
        w.stop()
    print('RESULT', json.dumps({k: log.get(k) for k in ('state', 'reason', 'mission', 'objective_after', 'run_id')}), flush=True)


if __name__ == '__main__':
    main()
