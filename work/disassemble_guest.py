"""Inspect a bounded range of the local private ELF; no dependencies beyond capstone."""
from pathlib import Path
import struct
import sys
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

blob = (Path(__file__).resolve().parents[1] / "game-mhfu/EBOOT.ELF").read_bytes()
phoff = struct.unpack_from("<I", blob, 28)[0]
phsize, phnum = struct.unpack_from("<HH", blob, 42)
address = int(sys.argv[1], 0)
length = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x200
assert 0 < length <= 0x4000
for i in range(phnum):
    ptype, offset, vaddr, _, size, *_ = struct.unpack_from("<8I", blob, phoff + i * phsize)
    if ptype == 1 and vaddr <= address and address + length <= vaddr + size:
        data = blob[offset + address - vaddr:offset + address - vaddr + length]
        cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
        cs.skipdata = True
        for ins in cs.disasm(data, address):
            print(f"{ins.address:08X}: {ins.mnemonic:10} {ins.op_str}")
        break
else:
    raise ValueError("Range outside the ELF load segments")
