from elfmem import *
import struct
from capstone import *
md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
EXP = 0x0890c740
# table d'export PSP : [NID x (funcs+vars)] puis [adresses x (funcs+vars)]
nids = [u32(EXP+4*i) for i in range(6)]
addrs= [u32(EXP+4*6+4*i) for i in range(6)]
known = {0xD632ACDB:'module_start',0xCEE8593C:'module_stop',0xF01D73A7:'module_info',
         0x2F064FA6:'module_reboot_before',0xD3744BE0:'module_bootstart',0x0F7C276C:'module_start_thread_parameter',
         0xCF0CC697:'module_stop_thread_parameter',0x11B97506:'module_sdk_version'}
for n,a in zip(nids,addrs):
    print(f"  NID 0x{n:08X} -> 0x{a:08x}   {known.get(n,'(inconnu)')}")
print("e_entry = 0x089094d4")

def listing(va, count):
    out = []
    for i in range(count):
        w = rd(va+4*i, 4)
        ins = list(md.disasm(w, va+4*i))
        txt = f"{ins[0].mnemonic:8s} {ins[0].op_str}" if ins else f".word 0x{struct.unpack('<I',w)[0]:08x}   ; (non décodé)"
        out.append(f"  {va+4*i:08x}:  {w.hex()}  {txt}")
    return '\n'.join(out)
print("\n== Désassemblage depuis e_entry (0x089094d4), 48 instructions ==")
print(listing(0x089094d4, 48))
