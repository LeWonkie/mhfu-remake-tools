from elfmem import *
import struct, collections, json
from capstone import *
TEXT_VA, TEXT_SZ = TEXT
code = rd(TEXT_VA, TEXT_SZ)
words = struct.unpack('<%dI' % (len(code)//4), code)
md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
md.detail = False

bad = collections.Counter(); ok = 0; badtotal = 0
badwords = {}
for i,w in enumerate(words):
    va = TEXT_VA + 4*i
    insn = list(md.disasm(struct.pack('<I', w), va))
    if insn: ok += 1
    else:
        badtotal += 1; bad[w >> 26] += 1; badwords.setdefault(w>>26, w)
print(f".text : {len(words)} mots | décodés par capstone MIPS32 : {ok} ({ok/len(words)*100:.2f} %) | non décodés : {badtotal}")
print("Non décodés par opcode primaire (6 bits) :")
for op,c in bad.most_common(14):
    print(f"   opcode 0x{op:02x} : {c:6d}   (exemple mot 0x{badwords[op]:08x})")

# cibles de jal  (opcode 3) et de j (opcode 2) -> adresses d'entrée de fonctions / sauts
jal = collections.Counter()
for i,w in enumerate(words):
    if (w >> 26) == 3:
        tgt = ((TEXT_VA + 4*i + 4) & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
        jal[tgt] += 1
in_text   = {t:c for t,c in jal.items() if TEXT_VA <= t < TEXT_VA+TEXT_SZ}
to_stub   = {t:c for t,c in jal.items() if STUBT[0] <= t < STUBT[0]+STUBT[1]}
other     = {t:c for t,c in jal.items() if t not in in_text and t not in to_stub}
print(f"\njal : {sum(jal.values())} appels, {len(jal)} cibles distinctes")
print(f"   vers .text           : {len(in_text)} fonctions candidates ({sum(in_text.values())} appels)")
print(f"   vers .sceStub.text   : {len(to_stub)} stubs systèmes ({sum(to_stub.values())} appels)")
print(f"   ailleurs (overlays ?): {len(other)} cibles ({sum(other.values())} appels)")
if other:
    print("   exemples hors .text/.stub :", [hex(t) for t in sorted(other)[:8]])
json.dump({'jal_in_text':{hex(k):v for k,v in in_text.items()},
           'jal_stub':{hex(k):v for k,v in to_stub.items()},
           'jal_other':{hex(k):v for k,v in other.items()}}, open('calltargets.json','w'))
