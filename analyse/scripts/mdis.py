"""Désassembleur annoté pour l'ELF PSP (capstone + noms de stubs)."""
from elfmem import *
import struct, json
from capstone import *
md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
_stubs = {int(k,16):v for k,v in json.load(open('stub_map.json')).items()}
def stubname(addr):
    if addr in _stubs:
        l,n,nm = _stubs[addr]; return f"{l}::{nm}" if nm else f"{l}::NID_{n:08X}"
    return None
def fmt(va):
    w = rd(va,4); wi = struct.unpack('<I',w)[0]
    ins = list(md.disasm(w, va))
    if not ins: return f"{va:08x}:  {wi:08x}  .word    0x{wi:08x}   ; non décodé (VFPU/Allegrex ?)"
    i = ins[0]; txt = f"{i.mnemonic:8s} {i.op_str}"
    note = ''
    if i.mnemonic in ('jal','j'):
        t = int(i.op_str, 16)
        note = '   ; -> ' + (stubname(t) or f"sub_{t:08x}")
    return f"{va:08x}:  {wi:08x}  {txt}{note}"
def func_end(va, maxn=4000):
    """Fin = premier 'jr $ra' + son delay slot (heuristique simple, sans analyse de flot)."""
    for k in range(maxn):
        w = struct.unpack('<I', rd(va+4*k,4))[0]
        if w == 0x03e00008: return va+4*k+8
    return va+4*maxn
def dump(va, end=None):
    end = end or func_end(va)
    return '\n'.join(fmt(a) for a in range(va, end, 4))
if __name__ == '__main__':
    import sys
    va = int(sys.argv[1],16); print(dump(va, int(sys.argv[2],16) if len(sys.argv)>2 else None))
