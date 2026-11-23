"""Name the unsupported instructions left in a translated.inc (static census).

Names follow PPSSPP Core/MIPS/MIPSTables.cpp (VFPU tables) at commit 0c6aa15.
Some reachable words are data decoded as code, so counts are an upper bound.
"""
import argparse
import collections
from pathlib import Path
import re

VFPU4 = {0: 'vmov', 1: 'vabs', 2: 'vneg', 3: 'vidt', 4: 'vsat0', 5: 'vsat1', 6: 'vzero', 7: 'vone',
         16: 'vrcp', 17: 'vrsq', 18: 'vsin', 19: 'vcos', 20: 'vexp2', 21: 'vlog2', 22: 'vsqrt', 23: 'vasin',
         24: 'vnrcp', 26: 'vnsin', 28: 'vrexp2'}
VFPU7 = {0: 'vrnds', 1: 'vrndi', 2: 'vrndf1', 3: 'vrndf2', 18: 'vf2h', 19: 'vh2f', 22: 'vsbz', 23: 'vlgb',
         24: 'vuc2ifs', 25: 'vc2i', 26: 'vus2i', 27: 'vs2i', 28: 'vi2uc', 29: 'vi2c', 30: 'vi2us', 31: 'vi2s'}
VFPU9 = {0: 'vsrt1', 1: 'vsrt2', 2: 'vbfy1', 3: 'vbfy2', 4: 'vocp', 5: 'vsocp', 6: 'vfad', 7: 'vavg',
         8: 'vsrt3', 9: 'vsrt4', 10: 'vsgn', 16: 'vmfvc', 17: 'vmtvc', 25: 'vt4444', 26: 'vt5551', 27: 'vt5650'}
SIZE = {0: '.s', 1: '.p', 2: '.t', 3: '.q'}


def name(w):
    op = w >> 26; size = SIZE[((w >> 7) & 1) | ((w >> 14) & 2)]
    if op == 0x18: return ['vadd', 'vsub', 'vsbn', '?', '?', '?', '?', 'vdiv'][(w >> 23) & 7] + size
    if op == 0x19: return ['vmul', 'vdot', 'vscl', '?', 'vhdp', 'vcrs', 'vdet', '?'][(w >> 23) & 7] + size
    if op == 0x1b: return ['vcmp', '?', 'vmin', 'vmax', '?', 'vscmp', 'vsge', 'vslt'][(w >> 23) & 7] + size
    if op == 0x34:
        sub, fn = (w >> 21) & 31, (w >> 16) & 31
        if sub == 0: return VFPU4.get(fn, 'vfpu4?') + size
        if sub == 1: return VFPU7.get(fn, 'vfpu7?') + size
        if sub == 2: return VFPU9.get(fn, 'vfpu9?') + size
        if sub == 3: return 'vcst' + size
        if 16 <= sub <= 19: return ['vf2in', 'vf2iz', 'vf2iu', 'vf2id'][sub - 16] + size
        if sub == 20: return 'vi2f' + size
        if sub == 21: return 'vcmov' + size
        if sub >= 24: return 'vwbn' + size
        return 'vfpu34?'
    if op == 0x37: return ['vpfxs', 'vpfxs', 'vpfxt', 'vpfxt', 'vpfxd', 'vpfxd', 'viim', 'vfim'][(w >> 23) & 7]
    if op == 0x3c:
        sub = (w >> 21) & 31
        if sub < 4: return 'vmmul' + size
        if sub < 16: return 'vtfm/vhtfm' + size
        if sub < 20: return 'vmscl' + size
        if sub < 24: return 'vcrsp/vqmul' + size
        if sub == 28: return {0: 'vmmov', 3: 'vmidt', 6: 'vmzero', 7: 'vmone'}.get((w >> 16) & 15, 'vmset?') + size
        if sub == 29: return 'vrot' + size
        return 'vfpu6?'
    if op == 0x12:
        rs = (w >> 21) & 31
        if rs == 8: return ['bvf', 'bvt', 'bvfl', 'bvtl'][(w >> 16) & 3]
        if rs == 3: return 'mfv/mfvc'
        if rs == 7: return 'mtv/mtvc'
        return 'cop2?'
    if op == 0x3f: return {0xffff0000: 'vnop', 0xffff040d: 'vflush', 0xffff0320: 'vsync'}.get(w, 'vfpu3f?')
    if op in (0x35, 0x3d): return ('lv' if op == 0x35 else 'sv') + ('r.q' if w & 2 else 'l.q')
    if op == 17: return f'cop1 rs={(w >> 21) & 31} fn={w & 63}'
    if op == 0: return f'special fn={w & 63}'
    return f'op=0x{op:02x}'


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('translated', type=Path); a = p.parse_args()
    words = re.findall(r'unsupported opcode ([0-9a-f]{8})', a.translated.read_text(encoding='utf-8'))
    counts = collections.Counter(name(int(w, 16)) for w in words)
    print(f'{len(words)} occurrences, {len(set(words))} distinct words')
    for n, c in counts.most_common(): print(f'{c:5d} {n}')


if __name__ == '__main__':
    main()
