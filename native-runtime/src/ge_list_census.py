"""Count GE commands in the lists a native run submitted (from its RAM capture).

Static read of each logged list start, following JUMP/CALL/RET, up to END.
Lists may have been rewritten after submission; this is a diagnostic only.
"""
import argparse
import collections
import json
from pathlib import Path
import struct

RAM = 0x08800000
NAMES = {0x04: 'PRIM', 0x05: 'BEZIER', 0x06: 'SPLINE', 0x07: 'BBOX', 0x0e: 'SIGNAL', 0xea: 'TRANSFER',
         0x0f: 'FINISH', 0x0c: 'END', 0x08: 'JUMP', 0x0a: 'CALL', 0x0b: 'RET', 0xc4: 'CLOAD'}


def walk(ram, start):
    counts = collections.Counter()
    pc, stack, base, offset = start, [], 0, 0
    for _ in range(65536):
        at = pc - RAM
        if not 0 <= at <= len(ram) - 4:
            counts['<out of RAM>'] += 1; break
        w = struct.unpack_from('<I', ram, at)[0]; cmd, arg = w >> 24, w & 0xffffff
        counts[NAMES.get(cmd, f'0x{cmd:02x}')] += 1; pc += 4
        if cmd == 0x10: base = (arg & 0xff0000) << 8
        elif cmd == 0x13: offset = arg << 8
        elif cmd in (8, 10):
            if cmd == 10: stack.append(pc)
            pc = ((base | arg) + offset) & 0x0ffffffc
        elif cmd == 11 and stack: pc = stack.pop()
        elif cmd == 0x0c: break
    return counts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--trace', type=Path, required=True)
    a = p.parse_args()
    ram = (a.capture/'capture.ram.bin').read_bytes()
    total = collections.Counter(); lists = []
    for line in a.trace.read_text(encoding='utf-8').splitlines():
        e = json.loads(line)
        if e.get('event') == 'ge_list':
            c = walk(ram, int(e['start'], 16)); total += c
            lists.append(dict(id=e['id'], start=e['start'], prims=c['PRIM'], commands=sum(c.values())))
    print(json.dumps(dict(lists=lists, total=dict(total.most_common(20))), indent=1))


if __name__ == '__main__':
    main()
