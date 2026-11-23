import struct, sys
data = open(sys.argv[1], 'rb').read()
elf = open(sys.argv[2], 'rb').read()
# ELF program headers (PSP ELF, little-endian, 32-bit)
phoff, = struct.unpack_from('<I', elf, 0x1C)
phentsize, phnum = struct.unpack_from('<HH', elf, 0x2A)
segs = []
for i in range(phnum):
    p_type, p_off, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, _ = struct.unpack_from('<8I', elf, phoff + i * phentsize)
    if p_type == 1:
        segs.append((p_vaddr, p_filesz, p_flags, p_off))
        print(f'seg vaddr={p_vaddr:#010x} filesz={p_filesz:#x} flags={p_flags:#x}')
words = struct.unpack_from('<%dI' % (len(data) // 4), data)
regs = {}
stores = []
for i, w in enumerate(words):
    op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
    imm = w & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm
    if op == 0x0F:
        regs[rt] = imm << 16
    elif op in (0x09, 0x0D) and rs in regs:
        regs[rt] = (regs[rs] + simm) & 0xFFFFFFFF if op == 0x09 else regs[rs] | imm
    elif op in (0x28, 0x29, 0x2B) and rs in regs:
        tgt = (regs[rs] + simm) & 0xFFFFFFFF
        stores.append((i * 4, tgt, {0x28: 1, 0x29: 2, 0x2B: 4}[op]))
    elif op not in (0x23, 0x21, 0x25, 0x24, 0x20) and rt in regs and op != 0:
        pass
# map stores into EBOOT segments
base = None
def in_seg(a):
    c = a & 0x3FFFFFFF
    for v, sz, fl, _ in segs:
        if v <= c < v + sz:
            return v, fl
    return None
hits = [(o, t, n, in_seg(t)) for o, t, n in stores]
eb = [h for h in hits if h[3]]
print('stores total', len(stores), 'into EBOOT segments', len(eb))
for o, t, n, s in eb:
    print(f'  preload+{o:#06x}: store{n*8} -> {t:#010x} (seg {s[0]:#x} flags {s[1]:#x})')
