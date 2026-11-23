"""Offline armor evidence; invoke the existing GPL mhff tools outside the native repo."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import mhfu_data
from PIL import Image, ImageDraw, ImageStat

OUT = ROOT / 'work/armor-assets-20261021'
PARTS = (
    ('head', 'hair', 0x08960750, 0x08A01FA1, 372, 374),
    ('chest', 'body', 0x08964B70, 0x08A0AC4E, 356, 358),
    ('arms', 'arm', 0x08968D10, 0x08A131BF, 350, 352),
    ('waist', 'wst', 0x0896CD48, 0x08A1B932, 348, 350),
    ('legs', 'reg', 0x08970D30, 0x08A23CA9, 357, 359),
)


def external(name):
    spec = importlib.util.spec_from_file_location('reference_' + name, ROOT / f'references/mhff/psp/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    OUT.mkdir(exist_ok=True)
    ram = (ROOT / 'work/step5-probe/q10001-ram.bin').read_bytes()
    disc = mhfu_data.DataBin.open()
    tmh, pmo = external('tmh'), external('pmo')
    records, panels = [], []
    for sex in ('m', 'f'):
        for part, folder, table, names, black_id, white_id in PARTS:
            pair = []
            for color, equip_id in (('black', black_id), ('white', white_id)):
                record_at = table - 0x08000000 + equip_id * 40
                models = struct.unpack_from('<2H', ram, record_at)
                model = models[0 if sex == 'm' else 1]
                text_at = names - 0x08000000 + struct.unpack_from('<I', ram, names - 0x08000000 + equip_id * 4)[0]
                name = ram[text_at:ram.index(b'\0', text_at)].decode('utf-8')
                asset = f'player/{sex}/{folder}/{sex}_{folder}{model:03}.pac'
                index = disc.find(asset)
                data = disc.read(index)
                count = struct.unpack_from('<I', data)[0]
                if not 0 < count < 16 or 4 + count * 8 > len(data):
                    raise ValueError('invalid PAC directory: ' + asset)
                target = OUT / f'{sex}-{part}-{color}'
                target.mkdir(exist_ok=True)
                (target / 'asset.pac').write_bytes(data)
                entry = dict(sex=sex, part=part, color=color, equip_id=equip_id, name=name,
                             model_id=model, file_id=index, asset=asset,
                             sha256=hashlib.sha256(data).hexdigest(), entries=[], textures=[], errors=[])
                files = {}
                for i in range(count):
                    at, size = struct.unpack_from('<II', data, 4 + i * 8)
                    if at + size > len(data):
                        raise ValueError('invalid PAC member: ' + asset)
                    body = data[at:at + size]
                    kind = 'pmo' if body.startswith(b'pmo\0') else 'tmh' if body.startswith(b'.TMH') else 'bin'
                    path = target / f'entry-{i:02}.{kind}'
                    path.write_bytes(body)
                    files[kind] = path
                    entry['entries'].append(dict(index=i, offset=at, size=size, kind=kind,
                                                 sha256=hashlib.sha256(body).hexdigest()))
                try:
                    if 'tmh' in files:
                        tmh.convert_tmh(str(files['tmh']), str(target / 'texture.mtl'))
                    if 'pmo' in files:
                        pmo.convert_pmo(str(files['pmo']), str(target / 'texture.mtl'), str(target / 'mesh.obj'))
                except Exception as error:
                    entry['errors'].append(repr(error))
                for path in sorted(target.glob('texture*.png')):
                    im = Image.open(path).convert('RGBA')
                    rgb = im.convert('RGB')
                    entry['textures'].append(dict(file=str(path.relative_to(OUT)), size=list(im.size),
                                                  mean_rgb=ImageStat.Stat(rgb).mean))
                    tile = Image.new('RGB', (320, 250), '#36363d')
                    preview = im.copy()
                    preview.thumbnail((310, 208), Image.Resampling.NEAREST)
                    tile.paste(preview, ((320-preview.width)//2, 32), preview)
                    ImageDraw.Draw(tile).text((8, 7), f'{sex} {part} {color} model {model}', fill='white')
                    pair.append(tile)
                obj = target / 'mesh.obj'
                if obj.exists():
                    lines = obj.read_text().splitlines()
                    entry['vertices'] = sum(line.startswith('v ') for line in lines)
                    entry['faces'] = sum(line.startswith('f ') for line in lines)
                records.append(entry)
                print(asset, name, entry.get('vertices'), entry['errors'], flush=True)
            panels.extend(pair)
    columns = 4
    sheet = Image.new('RGB', (320 * columns, 250 * ((len(panels)+columns-1)//columns)), '#18181e')
    for i, panel in enumerate(panels):
        sheet.paste(panel, ((i % columns)*320, (i // columns)*250))
    sheet.save(OUT / 'textures-contact.png')
    (OUT / 'assets.json').write_text(json.dumps(records, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print('REPORT', OUT, len(records), 'assets', len(panels), 'textures')


if __name__ == '__main__':
    main()
