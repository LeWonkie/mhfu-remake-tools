"""Cherche des fonctions autonomes : feuille, courte, sans VFPU, sans lw/sw hors pile ni gp, triées par nb d'appelants."""
from elfmem import *
import struct, json
from capstone import *
md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
ct = json.load(open('calltargets.json'))
cands = []
for k,callers in ct['jal_in_text'].items():
    va = int(k,16)
    words = []
    ok = False
    for n in range(48):
        w = struct.unpack('<I', rd(va+4*n,4))[0]
        words.append(w)
        if w == 0x03e00008:          # jr $ra
            words.append(struct.unpack('<I', rd(va+4*n+4,4))[0]); ok = True; break
    if not ok: continue
    ops = [w>>26 for w in words]
    if any(o in (0x03,0x02,0x32,0x36,0x3a,0x3e,0x35,0x3d,0x12,0x18,0x19,0x1b,0x34,0x37,0x3c) for o in ops): continue   # jal/j/VFPU
    if any(w & 0xfc1f07ff == 0x00000009 for w in words): continue   # jalr
    # accès mémoire (lb/lh/lw/lbu/lhu/sb/sh/sw : 0x20-0x2b)
    mem = [w for w in words if (w>>26) in (0x20,0x21,0x22,0x23,0x24,0x25,0x28,0x29,0x2b)]
    # interdit gp(28) ou lui adresses globales ; autorise a0..a3 comme base
    bad = any(((w>>21)&31) not in (4,5,6,7) for w in mem)
    if bad: continue
    if any(((w>>26)==0x0f) for w in words): continue    # lui : constante d'adresse potentielle
    cands.append((callers, len(words), va, len(mem)))
cands.sort(reverse=True)
print(f"{len(cands)} candidats feuille/autonomes. Top 25 par nombre d'appelants :")
for callers,n,va,m in cands[:25]:
    print(f"  sub_{va:08x}  appelants={callers:4d}  {n:2d} instr  accès mémoire(base a0-a3)={m}")
json.dump([(c,n,hex(v),m) for c,n,v,m in cands], open('candidates.json','w'))
