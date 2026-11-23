"""One bounded capture trial; isolated save, real item use, no mission-state writes."""
import json
import math
from pathlib import Path
import struct
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
    quest, seconds = 1020, 240
    trap_item = int(sys.argv[1]) if len(sys.argv) > 1 else 79
    workshop = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(seconds, cancel.set)
    timer.daemon = True
    timer.start()
    began = time.monotonic()
    log = {'quest': quest, 'trap_item': trap_item, 'success': False,
           'returned_to_village': False, 'steps': [], 'shots': [],
           'assisted': ['placement', 'health held', 'boss weakened', 'items supplied', 'boss position held']}
    session = None

    def step(kind, **details):
        event = dict(kind=kind, seconds=round(time.monotonic() - began, 2), **details)
        log['steps'].append(event)
        print(json.dumps(event), flush=True)
        if session:
            core._write_json(session.dir / 'capture-v2.json', log)

    try:
        scenario = {'name': 'campagne', 'label': 'Capture v2 isolated trial', 'debug': True,
                    'fast_boot': True, 'dump_overlays': True, 'script': campaign.BOOT,
                    'screenshot_every': 0, 'settings': {'video.fast_forward': 'toggle',
                    'video.fast_forward_speed': '2', 'video.fast_loading': '1'}}
        session = campaign.Session(workshop, '20261020-153034-ceda7b', seconds + 15,
                                   variant=core.register_rtc_variant('normal', 1609459200123456),
                                   scenario=scenario)
        session.cancel = cancel
        log['run_id'] = session.run_id
        pilot = pilot_mod.Pilot(session, cancel=cancel)

        def shot(name, frames=1):
            written = pilot.confirm_shots([pilot.shoot(name, frames)])
            log['shots'] += written
            if name not in written:
                raise zones.Unavailable('missing capture ' + name)

        session.arm_loadout('best')
        pilot.wait_village(60)
        shot('capture-village-before', 60)
        pilot.depart(quest, 60)
        pilot.wait_quest(quest, 60)
        session.ask('hold health on', 'hold health', 5)
        original = zones.mission_objectives(session, quest)
        log['objective_before'] = original
        if original['initial'][0] != {'flags': 0x11, 'target': 6, 'quantity': 1}:
            raise ValueError('expected ordinary Yian Kut-Ku hunt')
        graph, visited = {}, set()
        boss = None
        while session.alive():
            area = zones.current(session, timeout=4)
            stage = area['stage']
            visited.add(stage)
            graph[stage] = [e['destination'] for e in area['exits'] if zones.usable(e) and e['destination'] != stage]
            found = [m for m in zones.monsters(session) if m['type'] == 6 and m['health'] > 0]
            local = [m for m in found if m['stage'] == stage]
            if local:
                boss = local[0]
                break
            target = next((first_hop(graph, stage, m['stage']) for m in found
                           if first_hop(graph, stage, m['stage'])), None)
            hop = target or next_step(graph, visited, stage)
            if hop is None:
                raise zones.Unavailable('no route to boss')
            exit = next(e for e in area['exits'] if e['destination'] == hop and zones.usable(e))
            result = zones.traverse(session, area, exit, settle=0.5)
            step('area', source=stage, destination=hop, reached=result['reached'])
        if boss is None:
            raise zones.Unavailable('boss search stopped')
        slot = boss['slot']
        step('boss', monster=boss)
        session.ask('hold still on', 'hold still', 5)
        session.ask(f'goto monster {slot}', 'moved', 5)
        area = zones.current(session)
        zones.place(session, area['player'], (boss['position'][0] + 1400,
                                             boss['position'][1], boss['position'][2]))
        # Let the hunter settle on the floor before using any item.
        time.sleep(3)
        area = zones.current(session)
        shot('capture-boss-found')
        step('ready', hunter=area['position'], boss=boss['position'])

        def command(line, expected='pouch'):
            answer = session.ask(line, expected, 5)
            step('command', command=line, answer=answer)
            if not answer:
                raise zones.Unavailable('no answer: ' + line)
            return answer

        # Clear the private quest pouch, so each supplied item occupies the selected first slot.
        gamesys = zones.u32(session, zones.GAMESYS_PTR)
        raw = zones.read(session, gamesys + 0x6AEA6, 96)
        items = {struct.unpack_from('<H', raw, offset)[0] for offset in range(0, 96, 4)} - {0}
        pilot.ask([f'pouch {item} 0' for item in sorted(items)], ['pouch'] * len(items), 6)
        command(f'pouch {trap_item} 1')
        # Run item interactions at normal game speed for the finite trap window.
        session.inputs(['0:pad guide 1'])
        time.sleep(0.5)
        shot('capture-item-selected')
        # Trap is set by a real Square press at the hunter's feet.
        origin = zones.current(session)['position']
        for attempt in range(3):
            session.inputs(['0:pad x 6'])
            time.sleep(2)
            pouch = command('pouch', 'pouch:')
            if f' {trap_item}x' not in pouch:
                break
        shot('capture-after-item-use')
        if f' {trap_item}x' in pouch:
            raise zones.Unavailable('trap was not consumed')
        shot('capture-trap-set')
        origin_after = zones.current(session)['position']
        step('trap_set', before=origin, after=origin_after)
        command(f'monster {slot} hp 1', 'health set')
        command('pouch 74 10')

        def pin(position):
            # Reset hold state and place both position vectors in a single frame.
            lines = ['hold still off']
            for field in (zones.POSITION, zones.NEXT_POSITION):
                for index, value in enumerate(position):
                    bits = struct.unpack('<I', struct.pack('<f', value))[0]
                    lines.append(f'poke32 0x{boss["address"] + field + index * 4:08X} 0x{bits:08X}')
            lines.append('hold still on')
            workshop.debug_commands(lines)
            time.sleep(0.25)

        # Use the actual placement point, then nearby points to cover the trap's forward offset.
        # The hunter stays close enough for the Tranq Bomb's area of effect.
        for index, (dx, dz) in enumerate(((0, 0), (80, 0), (-80, 0), (0, 80), (0, -80))):
            pin((origin[0] + dx, origin_after[1], origin[2] + dz))
            session.inputs(['0:pad x 6'])
            time.sleep(1)
            mission = zones.mission_state(session)
            step('tranq', attempt=index, mission=mission,
                 boss=next((m for m in zones.monsters(session) if m['slot'] == slot), None))
            shot(f'capture-tranq-{index}')
            if zones.mission_over(mission):
                break
        log['mission_state'] = zones.mission_state(session)
        log['objective_after'] = zones.mission_objectives(session, quest)
        log['success'] = log['mission_state'] == zones.MISSION_CLEAR
        shot('capture-finished', 15)
        if log['success']:
            session.ask('hold still off', 'hold still', 5)
            step('capture_clear', mission=log['mission_state'], objective=log['objective_after'])
            pilot.ensure_fast_forward()
            returning = time.monotonic()
            last_press, last_shot = [0.0], [-10.0]

            def back():
                snap = pilot.snapshot()
                if snap['task'] == pilot_mod.VILLAGE_TASK:
                    return snap
                pilot.ask(['quest results'], ['results countdown'], 1)
                elapsed = time.monotonic() - returning
                if elapsed - last_shot[0] >= 8:
                    name = f'capture-return-{int(elapsed):03}'
                    shot(name)
                    step('return_screen', snapshot=snap, shot=name)
                    last_shot[0] = elapsed
                if time.monotonic() - last_press[0] >= 1:
                    session.inputs(['0:pad b 6'])
                    last_press[0] = time.monotonic()

            pilot.until('village', back, 90, poll=0.3)
            pilot.wait_village(20)
            log['returned_to_village'] = True
            log['village_ready'] = True
            shot('capture-village-after', 60)
        log['state'] = 'completed'
    except Exception as error:
        log.update(state='cancelled' if cancel.is_set() else 'incomplete', reason=repr(error))
        step('error', reason=repr(error))
    finally:
        timer.cancel()
        workshop.stop()
        log['duration_seconds'] = round(time.monotonic() - began, 2)
        if session:
            log['manifest'] = json.loads((session.dir / 'manifest.json').read_text(encoding='utf-8'))['state']
            core._write_json(session.dir / 'capture-v2.json', log)
        print('RESULT', json.dumps({k: log.get(k) for k in (
            'run_id', 'state', 'reason', 'mission_state', 'success', 'returned_to_village', 'manifest')}), flush=True)


if __name__ == '__main__':
    main()
