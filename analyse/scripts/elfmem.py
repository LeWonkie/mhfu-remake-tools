"""Chargeur mémoire minimal pour l'ELF PSP EBOOT.BIN (segment LOAD unique)."""
import struct
PATH = '/mnt/user-data/uploads/EBOOT.BIN'
E = open(PATH,'rb').read()
BASE_VA   = 0x08804000
BASE_OFF  = 0x25b4
FILESZ    = 0x1c2b60
def va2off(va):
    o = va - BASE_VA
    assert 0 <= o < FILESZ, hex(va)
    return BASE_OFF + o
def rd(va, n):  return E[va2off(va):va2off(va)+n]
def u32(va):    return struct.unpack('<I', rd(va,4))[0]
def u16(va):    return struct.unpack('<H', rd(va,2))[0]
def cstr(va):
    o = va2off(va); e = E.index(b'\0', o); return E[o:e].decode('latin1')
# sections utiles (adresse, taille) issues de readelf/analyze_headers.py
TEXT  = (0x08804000, 0x1078e0)
STUBT = (0x0890b8e0, 0x000910)
DATA  = (0x0890d38c, 0x0b97d4)
