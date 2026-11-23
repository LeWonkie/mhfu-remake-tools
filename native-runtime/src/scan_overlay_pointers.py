"""Address-taken function starts and jump tables inside observed overlays.

Reads <build>/overlays/observed-overlays.json and the verified overlay images it
names (already checked against DATA.BIN by observed_overlays.py). Same rules as
scan_function_pointers.py, restricted to each overlay's own code range: lui +
addiu/ori materialising a prologue address, data words pointing at a prologue or a stackless vtable constructor,
and runs of >= 3 consecutive data words pointing into the overlay code.
"""
import argparse
import json
from pathlib import Path
import struct


def scan_overlay(raw, load, code_start, text_end, include_unreferenced=False):
    word = lambda a: struct.unpack_from('<I', raw, a - load)[0]
    inside = lambda t: code_start <= t < text_end and not t & 3
    def start(t):
        if not inside(t): return False
        first = word(t)
        if first >> 16 == 0x27bd and first & 0x8000: return True
        # Stackless C++ constructor: materialise a vtable in v0, store to this.
        # game_sub 0x09c26db8 is address-taken but has no stack allocation.
        return (t + 12 <= text_end and first >> 16 == 0x3c02
                and word(t + 4) >> 16 in (0x2442, 0x3442)
                and word(t + 8) == 0xac820000)
    found = set()
    if include_unreferenced:
        # Relative/constructed method tables need not store absolute addresses.
        # Add recognisable entry prefixes only, never every text word.
        found.update(a for a in range(code_start, text_end, 4) if start(a))
        # Leaf methods have no prologue (stage066 0x09d5e2b8: jr ra; li v0,0),
        # so also take the first non-nop word after each jr ra + delay slot.
        for a in range(code_start, text_end - 4, 4):
            if word(a) != 0x03e00008: continue
            t = a + 8
            while t < text_end and word(t) == 0: t += 4
            if t < text_end: found.add(t)
    for a in range(code_start, text_end, 4):
        x = word(a)
        if x >> 26 != 15: continue
        rt, high = (x >> 16) & 31, (x & 0xffff) << 16
        for k in range(1, 9):
            if a + 4 * k >= text_end: break
            y = word(a + 4 * k); op = y >> 26
            if op in (9, 13) and (y >> 21) & 31 == rt:
                low = y & 0xffff
                t = (high + (low - 0x10000 if op == 9 and low & 0x8000 else low)) & 0xffffffff
                if start(t): found.add(t)
            if (y >> 16) & 31 == rt and op not in (0, 4, 5, 0x28, 0x29, 0x2b): break
    run = []
    for a in range(text_end, load + len(raw) - 3, 4):
        x = word(a)
        if start(x): found.add(x)
        if inside(x): run.append(x)
        else:
            if len(run) >= 3: found.update(run)
            run = []
    if len(run) >= 3: found.update(run)
    return found


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); folder = a.build/'overlays'
    manifest = json.loads((folder/'observed-overlays.json').read_text(encoding='utf-8'))
    roots = set(); summary = []
    for o in manifest['overlays']:
        raw = (folder/o['image']).read_bytes(); load = int(o['load'], 16)
        found = scan_overlay(raw, load, int(o['code_start'], 16), int(o['text_end'], 16))
        roots |= found; summary.append(f"{o['name']}:{len(found)}")
    a.out.write_text('\n'.join(f'0x{x:08x}' for x in sorted(roots)) + '\n', encoding='ascii')
    print(f'{len(roots)} overlay roots ({", ".join(summary)}) -> {a.out}')


if __name__ == '__main__':
    main()
