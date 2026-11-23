"""List EBOOT function starts whose address is taken (static, read-only).

A candidate is a word-aligned address in the main text segment whose first
instruction is a stack-frame prologue (addiu sp,sp,-N) and that is either
materialised in code (lui + addiu/ori within 8 instructions) or stored as a
data word. These are thread entries, callbacks and method pointers that the
runtime would otherwise discover one failed run at a time. Seeding them only
widens AOT coverage; no instruction semantics change.
"""
import argparse
import hashlib
from pathlib import Path
import struct

TEXT_END = 0x0890b8e0  # end of the audited EBOOT main text (stub table follows)


def scan(elf):
    phoff = struct.unpack_from('<I', elf, 28)[0]; size, count = struct.unpack_from('<HH', elf, 42)
    load = next(p for p in (struct.unpack_from('<8I', elf, phoff + i * size) for i in range(count)) if p[0] == 1)
    offset, va, filesz = load[1], load[2], load[4]; image = elf[offset:offset + filesz]
    word = lambda a: struct.unpack_from('<I', image, a - va)[0]

    def start(t):
        return va <= t < TEXT_END and not t & 3 and (word(t) >> 16) == 0x27bd and word(t) & 0x8000

    found = set()
    for a in range(va, TEXT_END, 4):
        x = word(a)
        if x >> 26 != 15: continue
        rt, high = (x >> 16) & 31, (x & 0xffff) << 16
        for k in range(1, 9):
            y = word(a + 4 * k); op = y >> 26
            if op in (9, 13) and (y >> 21) & 31 == rt:
                low = y & 0xffff
                target = (high + (low - 0x10000 if op == 9 and low & 0x8000 else low)) & 0xffffffff
                if start(target): found.add(target)
            if (y >> 16) & 31 == rt and op not in (0, 4, 5, 0x28, 0x29, 0x2b): break
    run = []
    for i in range(TEXT_END - va, len(image) - 3, 4):
        x = struct.unpack_from('<I', image, i)[0]
        if start(x): found.add(x)
        # Jump/method tables: runs of >= 3 consecutive data words pointing into text.
        if va <= x < TEXT_END and not x & 3: run.append(x)
        else:
            if len(run) >= 3: found.update(run)
            run = []
    if len(run) >= 3: found.update(run)
    return sorted(found)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); elf = a.elf.read_bytes()
    if hashlib.sha256(elf).hexdigest() != '02811bb3a529d48d707072a726e87106d0ecdab62aa7e494b23ecf1469eb662f':
        raise ValueError('Only the audited EBOOT build is supported')
    roots = scan(elf); a.out.write_text('\n'.join(f'0x{x:08x}' for x in roots) + '\n', encoding='ascii')
    print(f'{len(roots)} function-pointer roots -> {a.out}')


if __name__ == '__main__':
    main()
