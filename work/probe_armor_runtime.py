"""Bounded isolated armor IO/texture/RAM evidence; no save originals written."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_core as core
import atelier_campaign as campaign
import atelier_pilot as pilot_mod
import mhfu_data


def main():
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    output = ROOT / 'work' / ('armor-runtime-' + stamp)
    output.mkdir()
    textures = output / 'textures'
    core.VARIANTS['armor_trace'] = {'label': 'Armor asset trace', 'env': {
        'MHP3RD_TRACE_IO': '1', 'MHP3RD_TEXTURE_DUMP': str(textures),
        'MHP3RD_TRACE_TEXTURE_PACK': '1'}}
    workshop = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(120, cancel.set)
    timer.daemon = True
    timer.start()
    log = dict(state='starting', output=str(output))
    session = None
    try:
        scenario = {'name': 'campagne', 'label': 'Armor asset trace', 'fast_boot': True, 'debug': True,
                    'script': campaign.BOOT, 'screenshot_every': 0,
                    'settings': {'video.fast_forward': 'toggle', 'video.fast_forward_speed': '2',
                                 'video.fast_loading': '1'}}
        session = campaign.Session(workshop, '20261020-153034-ceda7b', 135,
                                   variant=core.register_rtc_variant('armor_trace', 1609459200123456),
                                   scenario=scenario)
        session.cancel = cancel
        log['run_id'] = session.run_id
        print('RUN', session.run_id, str(output), flush=True)
        pilot = pilot_mod.Pilot(session, cancel=cancel)
        log['loadout_armed'] = session.arm_loadout('best')
        pilot.wait_village(60)
        log['shots'] = pilot.confirm_shots([pilot.shoot('armor-village', 60)])
        log['equipment_answer'] = session.ask('equip', 'worn ', 5)
        filename = 'armor-' + session.run_id + '.bin'
        log['dump_answer'] = session.ask('dump ' + filename, 'dumped', 15)
        source = workshop.executable.parent / filename
        destination = session.dir / 'armor-village-ram.bin'
        if not source.is_file() or source.stat().st_size != 64*1024*1024:
            raise ValueError('missing or incomplete RAM evidence')
        source.replace(destination)
        log['ram'] = str(destination)
        log['state'] = 'completed'
    except Exception as error:
        log.update(state='incomplete', reason=repr(error))
    finally:
        timer.cancel()
        workshop.stop()
    if session:
        disc = mhfu_data.DataBin.open()
        reads = []
        trace = (session.dir / 'stderr.txt').read_text(encoding='utf-8', errors='replace')
        for match in re.finditer(r'\[io\] read fd=\d+ (\S*DATA\.BIN) offset=(\d+) size=(\d+) -> (0x[0-9A-Fa-f]+)', trace):
            offset, size = int(match[2]), int(match[3])
            file_id = disc.file_at(disc.base + offset)
            name = disc.name(file_id) if file_id is not None else ''
            if name.startswith('player/'):
                reads.append(dict(file_id=file_id, asset=name, offset=offset, size=size, address=match[4]))
        log['player_asset_reads'] = reads
        if log.get('ram'):
            ram = Path(log['ram']).read_bytes()
            assets = json.loads((ROOT / 'work/armor-assets-20261021/assets.json').read_text(encoding='utf-8'))
            matches = []
            for asset in assets:
                tmh = next(e for e in asset['entries'] if e['kind'] == 'tmh')
                folder = ROOT / 'work/armor-assets-20261021' / f"{asset['sex']}-{asset['part']}-{asset['color']}"
                data = (folder / f"entry-{tmh['index']:02}.tmh").read_bytes()
                # Match a full texture bank, and a long pixel fragment if its header was relocated.
                full = ram.find(data)
                fragment = ram.find(data[48:304])
                matches.append(dict(sex=asset['sex'], part=asset['part'], color=asset['color'],
                                    full_address=hex(0x08000000+full) if full>=0 else None,
                                    fragment_address=hex(0x08000000+fragment) if fragment>=0 else None))
            log['texture_ram_matches'] = matches
        core._write_json(session.dir / 'armor-runtime.json', log)
    core._write_json(output / 'report.json', log)
    print(json.dumps(log, indent=2), flush=True)


if __name__ == '__main__':
    os.environ.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory',
                      GIT_CONFIG_VALUE_0=str(ROOT / 'psprecomp-mhfu'))
    main()
