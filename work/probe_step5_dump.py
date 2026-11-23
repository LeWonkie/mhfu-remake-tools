"""Bounded probe: depart one quest, dump guest RAM once in its first area, stop. Reads only."""
import argparse
from pathlib import Path
import struct
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
sys.path.insert(0, str(ROOT / 'work'))
import atelier_campaign as campaign
import atelier_core as core
import atelier_pilot as pilot_mod
import atelier_zones as zones
from probe_step5 import resources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('quest', type=int)
    parser.add_argument('--seconds', type=int, default=150)
    args = parser.parse_args()
    w = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(args.seconds, cancel.set); timer.daemon = True; timer.start()
    folder = ROOT / 'work/step5-probe'; folder.mkdir(exist_ok=True)
    s = None; info = {'quest': args.quest}
    try:
        scenario = {'name': 'campagne', 'label': 'Step 5 RAM dump', 'debug': True, 'fast_boot': True,
                    'dump_overlays': True, 'script': campaign.BOOT, 'screenshot_every': 0,
                    'settings': {'video.fast_forward': 'toggle', 'video.fast_forward_speed': '8',
                                 'video.fast_loading': '1'}}
        s = campaign.Session(w, '20261020-153034-ceda7b', args.seconds + 15,
                             variant=core.register_rtc_variant('normal', 1609459200123456), scenario=scenario)
        s.cancel = cancel
        p = pilot_mod.Pilot(s, cancel=cancel)
        s.arm_loadout('best'); p.wait_village(60)
        p.ensure_fast_forward()
        # Without this pause the departure stayed at scene 0x1D with task 10 (run 20261021-163610-6048bc).
        p.confirm_shots([p.shoot('dump-village', 60)])
        p.depart(args.quest, 60); p.wait_quest(args.quest, 60)
        area = zones.current(s)
        quest = zones.u32(s, zones.QUEST_PTR)
        manager = zones.u32(s, zones.STAGE_MANAGER_PTR)
        info.update(run_id=s.run_id, stage=area['stage'], quest_ptr=quest,
                    map_id=zones.u32(s, manager + zones.MAP_ID),
                    objective=zones.mission_objectives(s, args.quest),
                    resources=resources(s, area))
        dump = s.dir / f'q{args.quest}-ram.bin'
        info['dump_answer'] = s.ask(f'dump {dump.name}', 'dumped', 60)
        info['dump_path'] = str(dump)
        print('INFO', info, flush=True)
    finally:
        timer.cancel()
        core._write_json(folder / f'dump-{args.quest}.json', info)
        w.stop()


if __name__ == '__main__': main()
