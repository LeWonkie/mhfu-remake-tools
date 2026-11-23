"""List EBOOT imports whose stub address no native HLE case handles.

Static census only: a stub is "handled" when its address appears as a case
label (or comparison) in the native runtime sources. Resident-module
redirections and table-driven services are reported separately by name.
"""
import argparse
import csv
import json
from pathlib import Path
import re


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--imports', type=Path, required=True, help='EBOOT.BIN.imports.csv from a build folder')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    src = Path(__file__).parent
    text = ''.join(f.read_text(encoding='utf-8', errors='replace') for f in sorted(src.glob('native_*.[ch]')))
    seen = {0x08900000 + int(x, 16) for x in re.findall(r'0x0?890([0-9a-fA-F]{4})(?![0-9a-fA-F])', text)}
    rows = list(csv.DictReader(a.imports.open(encoding='utf-8')))
    missing, handled = [], []
    for row in rows:
        stub = int(row['stub_va'], 16)
        item = dict(stub=f'0x{stub:08x}', library=row['library'], nid=row['nid'], name=row.get('name') or '')
        (handled if stub in seen else missing).append(item)
    missing.sort(key=lambda x: (x['library'], x['name']))
    report = dict(imports=len(rows), handled=len(handled), missing=len(missing),
                  scope='Static stub-address census of native_*.c/h; not a proof that a handled service covers every mode',
                  missing_imports=missing)
    a.out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    for x in missing: print(x['stub'], x['library'], x['nid'], x['name'])
    print(f"{len(rows)} imports, {len(handled)} handled, {len(missing)} missing")


if __name__ == '__main__':
    main()
