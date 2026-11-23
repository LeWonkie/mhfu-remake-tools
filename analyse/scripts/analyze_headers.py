#!/usr/bin/env python3
"""Analyse compacte de EBOOT.BIN / BOOT.BIN : ELF, sections, entropie, comparaison."""
import struct, math, hashlib, collections, sys

def entropy(b):
    if not b: return 0.0
    c = collections.Counter(b); n = len(b)
    return -sum(v/n*math.log2(v/n) for v in c.values())

E = open('/mnt/user-data/uploads/EBOOT.BIN','rb').read()
B = open('/mnt/user-data/uploads/BOOT.BIN','rb').read()

print("== Tailles / hash ==")
for n,d in (('EBOOT.BIN',E),('BOOT.BIN',B)):
    print(f"{n}: {len(d)} octets  sha256={hashlib.sha256(d).hexdigest()}")
    print(f"   entropie globale = {entropy(d):.4f} bits/octet, octets nuls = {d.count(0)/len(d)*100:.2f} %")

# --- ELF header
(e_ident,e_type,e_machine,e_version,e_entry,e_phoff,e_shoff,e_flags,
 e_ehsize,e_phentsize,e_phnum,e_shentsize,e_shnum,e_shstrndx) = struct.unpack('<16sHHIIIIIHHHHHH', E[:52])
print("\n== En-tête ELF EBOOT ==")
print(f"type={e_type} machine={e_machine} entry=0x{e_entry:08x} flags=0x{e_flags:08x}")
print(f"phoff={e_phoff} phnum={e_phnum} shoff={e_shoff} shnum={e_shnum} shstrndx={e_shstrndx}")

# --- Program headers
ph = []
for i in range(e_phnum):
    o = e_phoff + i*e_phentsize
    ph.append(struct.unpack('<8I', E[o:o+32]))
types = collections.Counter(p[0] for p in ph)
print("\n== Program headers par type ==", dict(types))
for i,p in enumerate(ph[:3]):
    t,off,va,pa,fs,ms,fl,al = p
    print(f"PH[{i}] type={t} off=0x{off:x} vaddr=0x{va:08x} paddr=0x{pa:08x} filesz=0x{fs:x} memsz=0x{ms:x} flags=0x{fl:x}")

# --- Section headers
sh = []
for i in range(e_shnum):
    o = e_shoff + i*e_shentsize
    sh.append(struct.unpack('<10I', E[o:o+40]))
stroff = sh[e_shstrndx][4]
def name(n):
    e = E.index(b'\0', stroff+n); return E[stroff+n:e].decode('latin1')
secs = [(name(s[0]),)+s[1:] for s in sh]

print("\n== Sections 'principales' (non .ovl) ==")
print(f"{'nom':28s} {'type':>10s} {'flags':>6s} {'addr':>10s} {'offset':>8s} {'size':>8s}")
for s in secs:
    if not s[0].endswith('.ovl'):
        print(f"{s[0]:28s} 0x{s[1]:08x} 0x{s[2]:04x} 0x{s[3]:08x} 0x{s[4]:06x} 0x{s[5]:06x}")

ovl = [s for s in secs if s[0].endswith('.ovl')]
print(f"\n== Sections .ovl : {len(ovl)} au total ==")
groups = collections.Counter(''.join(c for c in s[0].split('.')[0] if not c.isdigit()) for s in ovl)
print(dict(groups))
print("Exemple :")
for s in ovl[:6]+ovl[-3:]:
    print(f"  {s[0]:22s} addr=0x{s[3]:08x} off=0x{s[4]:06x} size=0x{s[5]:06x}")
