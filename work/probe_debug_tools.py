"""Bounded in-game check of the debug tools and of real gathering (step 5).

Quest 10001: area 98 -> 99 -> 92 -> 93, `quest gather`, `goto gather N` and
real circle presses at the two Mountain Herbs points, back to the camp, the
red box. Then quest 2002: `quest items` and the red box. Writes
debug-tools.json in the run folder and work/step5-probe/debug-tools.json.
"""
import json
from pathlib import Path
import re
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_campaign as campaign
import atelier_core as core
import atelier_pilot as pilot_mod
import atelier_zones as zones
import deliver

HERBS = 173
POUCH = re.compile(r'(\d+)x(-?\d+)')


def collect(s, line, done, timeout=6.0):
    """Sends one command and returns its answer lines once `done(lines)` holds."""
    s.new_lines()
    s.w.debug_command(line)
    lines, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline and s.alive():
        lines += [l[len('[debug] '):] for l in s.new_lines() if l.startswith('[debug] ') and not l.startswith('[debug] > ')]
        if lines and done(lines):
            break
        time.sleep(0.2)
    return lines


def pouch(s):
    answer = s.ask('pouch', 'pouch:', 6) or ''
    return {int(i): int(n) for i, n in POUCH.findall(answer.split('pouch:', 1)[-1])}


def hop(s, route, log):
    for destination in route:
        here = zones.current(s)
        exit = next(e for e in here['exits'] if e['destination'] == destination and zones.usable(e))
        result = zones.traverse(s, here, exit, settle=1)
        log.append({'from': here['stage'], 'to': destination, 'reached': result['reached']})
        if not result['reached']:
            raise zones.Unavailable(f'could not reach area {destination}: {result}')


def red_box(s, p, log, name):
    area = zones.current(s)
    box = deliver.delivery_box(s, area)
    x, y, z = box['position']
    zones.place(s, area['player'], (x - min(50, box['radius'] / 4), y, z))
    p.until('red box detected', lambda: zones.u32(s, area['player'] + deliver.PLAYER_INTERACTION) == box['address'], 10)
    s.inputs(['0:pad b 6'])
    ended = p.until('quest over', lambda: (lambda snap: snap if p.mission_over(snap) else None)(p.snapshot()), 20)
    log[name + '_mission'] = ended['mission']
    log['shots'] += p.confirm_shots([p.shoot(name + '-finished', 15)])

    def back():
        snap = p.snapshot()
        if snap['task'] == pilot_mod.VILLAGE_TASK:
            return snap
        p.ask(['quest results'], ['results countdown'], 1)
        s.inputs(['0:pad b 6'])
    p.until('village', back, 90, poll=0.3)
    p.wait_village(30)
    log[name + '_village'] = True
    log['shots'] += p.confirm_shots([p.shoot(name + '-village', 60)])


def main():
    seconds = 420
    w = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(seconds, cancel.set); timer.daemon = True; timer.start()
    log = {'state': 'starting', 'hops': [], 'gathering': [], 'shots': []}
    s = None
    try:
        scenario = {'name': 'campagne', 'label': 'Debug tools check', 'debug': True, 'fast_boot': True,
                    'dump_overlays': True, 'script': campaign.BOOT, 'screenshot_every': 0,
                    'settings': {'video.fast_forward': 'toggle', 'video.fast_forward_speed': '8',
                                 'video.fast_loading': '1'}}
        s = campaign.Session(w, '20261020-153034-ceda7b', seconds + 15,
                             variant=core.register_rtc_variant('normal', 1609459200123456), scenario=scenario)
        s.cancel = cancel
        log['run_id'] = s.run_id
        p = pilot_mod.Pilot(s, cancel=cancel)
        s.arm_loadout('best'); p.wait_village(60)
        p.ensure_fast_forward()
        log['item_find'] = collect(s, 'item find mountain', lambda l: any('Mountain' in x or 'no item' in x for x in l))
        log['item_8'] = s.ask('item 8', 'item ', 6)
        log['box_put'] = s.ask('box 173 3', 'box', 6)
        log['box'] = s.ask('box', 'box:', 6)
        log['box_remove'] = s.ask('box 173 0', 'box', 6)
        log['shots'] += p.confirm_shots([p.shoot('tools-village', 60)])

        # Quest 10001: real gathering.
        p.depart(10001, 60); p.wait_quest(10001, 60)
        s.ask('hold health on', 'health', 5)
        log['camp_gather'] = collect(s, 'quest gather', lambda l: any('gathering points' in x for x in l))
        log['pouch_start'] = pouch(s)
        hop(s, [99, 92, 93], log['hops'])
        listing = collect(s, 'quest gather', lambda l: any('gathering points' in x for x in l)
                          and len(l) > int(re.search(r': (\d+) gathering', next(x for x in l if 'gathering points' in x))[1]))
        log['area93_gather'] = listing
        herbs = [int(m[1]) for x in listing if (m := re.match(r'gather (\d+) .*table \d+: 173 Mountain Herbs 100%$', x))]
        log['herb_points'] = herbs
        for index in herbs:
            log['gathering'].append({'goto': s.ask(f'goto gather {index}', 'hunter moved', 6)})
            time.sleep(0.5)
            for press in range(6):
                before = pouch(s).get(HERBS, 0)
                s.inputs(['0:pad b 6'])
                deadline, after = time.monotonic() + 6, before
                while time.monotonic() < deadline and after == before:
                    time.sleep(0.4)
                    after = pouch(s).get(HERBS, 0)
                log['gathering'].append({'point': index, 'press': press, 'herbs_before': before, 'herbs_after': after})
                if after == before:
                    break
            log['gathering'].append({'point': index, 'listing_after': collect(
                s, 'quest gather', lambda l: any('gathering points' in x for x in l) and len(l) > 2)})
        log['shots'] += p.confirm_shots([p.shoot('tools-gathered', 15)])
        log['pouch_gathered'] = pouch(s)
        log['herbs_gathered'] = log['pouch_gathered'].get(HERBS, 0) - log['pouch_start'].get(HERBS, 0)
        log['objective_before_delivery'] = zones.mission_objectives(s, 10001)
        if log['pouch_gathered'].get(HERBS, 0) < 5:
            log['topped_up_with_quest_items'] = s.ask('quest items', 'quest items', 6)
        hop(s, [92, 99, 98], log['hops'])
        red_box(s, p, log, 'q10001')

        # Quest 2002: `quest items` only.
        p.confirm_shots([p.shoot('tools-before-2002', 60)])
        p.depart(2002, 60); p.wait_quest(2002, 60)
        log['objective_2002'] = zones.mission_objectives(s, 2002)
        log['quest_items_2002'] = s.ask('quest items', 'quest items', 6)
        red_box(s, p, log, 'q2002')
        log['state'] = 'done'
    except (pilot_mod.Stopped, pilot_mod.WaitTimeout, zones.Unavailable, ValueError, StopIteration) as error:
        log.update(state='cancelled' if cancel.is_set() else 'incomplete', reason=repr(error))
    finally:
        timer.cancel()
        if s:
            core._write_json(s.dir / 'debug-tools.json', log)
        core._write_json(ROOT / 'work/step5-probe/debug-tools.json', log)
        w.stop()
    print('RESULT', json.dumps(log)[:3000], flush=True)


if __name__ == '__main__':
    main()
