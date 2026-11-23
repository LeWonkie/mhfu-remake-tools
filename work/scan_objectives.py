"""Bounded scan: read the mission flags and objectives of quests, one session.

For each quest: departure, the Quest's mission flags (+0x28) and descriptors
(+0x728), then `quest time 1` so the quest ends and the hunter comes back.
Stops at the first descriptor that is neither slay (0x11), hunt (0x211) nor
delivery (0x12), or at the time budget. Reads only, apart from the clock.
Usage: scan_objectives.py SECONDS ID [ID...]
"""
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_campaign as campaign
import atelier_core as core
import atelier_pilot as pilot_mod
import atelier_zones as zones

KNOWN = {0x11, 0x12, 0x211}


def main():
    seconds, quests = int(sys.argv[1]), [int(q) for q in sys.argv[2:]]
    assert 60 <= seconds <= 1800 and quests
    w = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(seconds, cancel.set); timer.daemon = True; timer.start()
    out = ROOT / 'work/step5-probe/objectives-scan.json'
    found = json.loads(out.read_text(encoding='utf-8')) if out.exists() else {}
    s = None
    try:
        scenario = {'name': 'campagne', 'label': 'Objective scan', 'debug': True, 'fast_boot': True,
                    'dump_overlays': True, 'script': campaign.BOOT, 'screenshot_every': 0,
                    'settings': {'video.fast_forward': 'toggle', 'video.fast_forward_speed': '8',
                                 'video.fast_loading': '1'}}
        s = campaign.Session(w, '20261020-153034-ceda7b', seconds + 15,
                             variant=core.register_rtc_variant('normal', 1609459200123456), scenario=scenario)
        s.cancel = cancel
        p = pilot_mod.Pilot(s, cancel=cancel)
        s.arm_loadout('best'); p.wait_village(60)
        p.ensure_fast_forward()
        for quest in quests:
            if str(quest) in found:
                continue
            p.confirm_shots([p.shoot(f'scan-{quest}-village', 60)])
            p.depart(quest, 60)
            loaded = p.wait_quest(quest, 90)
            info = zones.mission_objectives(s, quest)
            q = zones.u32(s, zones.QUEST_PTR)
            entry = {'run_id': s.run_id, 'stage': loaded['stage'], 'mission_flags': hex(zones.u32(s, q + 0x28)),
                     'objectives': [(hex(e['flags']), e['target'], e['quantity']) for e in info['initial'] if e['flags']],
                     'targets': zones.quest_targets(s)}
            found[str(quest)] = entry
            core._write_json(out, found)
            print('QUEST', quest, entry, flush=True)
            if any(int(f, 16) not in KNOWN for f, _, _ in entry['objectives']):
                p.confirm_shots([p.shoot(f'scan-{quest}-quest', 15)])
                print('NEW KIND', quest, flush=True)
                break
            s.ask('quest time 1', 'quest time', 5)

            def back():
                snap = p.snapshot()
                if snap['task'] == pilot_mod.VILLAGE_TASK:
                    return snap
                p.ask(['quest results'], ['results countdown'], 1)
                s.inputs(['0:pad b 6'])
            p.until('village', back, 120, poll=0.3)
            p.wait_village(30)
    except (pilot_mod.Stopped, pilot_mod.WaitTimeout, zones.Unavailable, ValueError) as error:
        print('STOPPED', repr(error), flush=True)
    finally:
        timer.cancel()
        core._write_json(out, found)
        w.stop()


if __name__ == '__main__':
    main()
