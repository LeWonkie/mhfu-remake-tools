import struct, sys
elf = open(sys.argv[1], 'rb').read()
shoff, = struct.unpack_from('<I', elf, 0x20)
shentsize, shnum, shstrndx = struct.unpack_from('<HHH', elf, 0x2E)
if not shoff or not shnum:
    print('no section headers'); sys.exit()
secs = [struct.unpack_from('<10I', elf, shoff + i * shentsize) for i in range(shnum)]
strtab = secs[shstrndx]
for s in secs:
    name_off, typ, flags, addr, off, size = s[:6]
    name = elf[strtab[4] + name_off:].split(b'\0')[0].decode()
    if addr:
        print(f'{name:20} addr={addr:#010x} size={size:#08x} end={addr+size:#010x} exec={(flags & 4) != 0}')
