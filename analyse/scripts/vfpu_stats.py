"""Quantifie l'usage du VFPU dans .text (opcodes primaires confirmés par PPSSPP MIPSTables.cpp)."""
from elfmem import *
import struct, json, bisect, collections
TEXT_VA, TEXT_SZ = TEXT
words = struct.unpack('<%dI' % (TEXT_SZ//4), rd(TEXT_VA, TEXT_SZ))
VFPU_ARITH = {0x18:'VFPU0',0x19:'VFPU1',0x1b:'VFPU3',0x34:'VFPU4Jump',0x37:'VFPU5',0x3c:'VFPU6'}
VFPU_MEM   = {0x32:'lv.s',0x36:'lv.q',0x3a:'sv.s',0x3e:'sv.q'}
# 0x35 'lv' / 0x3d 'sv' (lvl/lvr, svl/svr) : aussi VFPU mem (variantes gauche/droite)
VFPU_MEM.update({0x35:'lv(l/r)',0x3d:'sv(l/r)'})
cnt = collections.Counter(); vfpu_idx = []
for i,w in enumerate(words):
    op = w>>26
    if op in VFPU_ARITH: cnt[VFPU_ARITH[op]]+=1; vfpu_idx.append(i)
    elif op in VFPU_MEM: cnt[VFPU_MEM[op]]+=1; vfpu_idx.append(i)
    elif op == 0x12:     cnt['COP2(mfv/mtv/bvf..)']+=1; vfpu_idx.append(i)
tot = sum(cnt.values())
print(f"Mots VFPU/COP2 dans .text : {tot} sur {len(words)} ({tot/len(words)*100:.2f} %)")
for k,v in cnt.most_common(): print(f"   {k:22s} {v:6d}")
# fonctions : points d'entrée connus = cibles de jal dans .text + exports
ct = json.load(open('calltargets.json'))
starts = sorted(set(int(k,16) for k in ct['jal_in_text']) | {0x089094d4, 0x089095e8})
def fn_of(va):
    j = bisect.bisect_right(starts, va)-1
    return starts[j] if j>=0 else None
per = collections.Counter(fn_of(TEXT_VA+4*i) for i in vfpu_idx)
print(f"\nFonctions (délimitées par cibles de jal) contenant du VFPU : {len(per)} sur {len(starts)}")
print("Top 8 par nombre de mots VFPU :")
for f,c in per.most_common(8):
    nxt = starts[bisect.bisect_right(starts,f)] if bisect.bisect_right(starts,f) < len(starts) else TEXT_VA+TEXT_SZ
    print(f"   sub_{f:08x}  {c:4d} mots VFPU / {(nxt-f)//4:4d} mots   ({c/((nxt-f)//4)*100:.0f} %)   appels entrants={ct['jal_in_text'].get(hex(f),0)}")
