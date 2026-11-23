#!/usr/bin/env python3
"""
Test différentiel : octets MIPS ORIGINAUX (EBOOT.BIN, émulés par Unicorn)  vs  réimplémentation C (libmhfu.so).
Écrit aussi golden.bin : vecteurs (entrées + sorties de l'ORIGINAL) rejouables sans Unicorn par test_golden.c.

Usage : python3 diff_test.py [chemin/EBOOT.BIN]
"""
import sys, struct, random, ctypes, os
import unicorn.mips_const as mc
from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN

EBOOT = sys.argv[1] if len(sys.argv) > 1 else '/mnt/user-data/uploads/EBOOT.BIN'
E = open(EBOOT, 'rb').read()
BASE_VA, BASE_OFF = 0x08804000, 0x25b4            # segment LOAD unique de l'ELF (readelf -l)
def va2off(va): return BASE_OFF + (va - BASE_VA)

def func_bytes(va):
    """Octets de la fonction : jusqu'au premier 'jr $ra' (0x03e00008) INCLUS + son delay slot."""
    n = 0
    while True:
        w = struct.unpack_from('<I', E, va2off(va) + 4*n)[0]
        n += 1
        if w == 0x03e00008:
            n += 1; break
        assert n < 256, "pas de jr ra trouvé"
    return E[va2off(va): va2off(va) + 4*n]

# --- machine virtuelle -------------------------------------------------
CODE_BASE, DATA_BASE, STACK_TOP, RET = 0x08800000, 0x08a00000, 0x09f08000, 0x08800100
class Machine:
    def __init__(self, va):
        self.va = va; self.code = func_bytes(va)
        self.mu = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        self.mu.mem_map(0x08800000, 0x200000); self.mu.mem_map(DATA_BASE, 0x10000); self.mu.mem_map(0x09f00000, 0x10000)
        self.mu.mem_write(va, self.code)
    def call(self, a0=0, a1=0, a2=0, a3=0, mem=()):
        mu = self.mu
        mu.mem_write(DATA_BASE, b'\x00' * 0x2000)
        for addr, data in mem: mu.mem_write(addr, data)
        for r, v in ((mc.UC_MIPS_REG_4,a0),(mc.UC_MIPS_REG_5,a1),(mc.UC_MIPS_REG_6,a2),(mc.UC_MIPS_REG_7,a3)):
            mu.reg_write(r, v & 0xffffffff)
        mu.reg_write(mc.UC_MIPS_REG_RA, RET); mu.reg_write(mc.UC_MIPS_REG_SP, STACK_TOP)
        mu.reg_write(mc.UC_MIPS_REG_2, 0xDEADBEEF)          # v0 sentinelle
        mu.emu_start(self.va, RET, count=100000)
        return mu
    def v0(self, mu):
        x = mu.reg_read(mc.UC_MIPS_REG_2); return x - (1<<32) if x & 0x80000000 else x
    def f0bits(self, mu): return mu.reg_read(mc.UC_MIPS_REG_F0) & 0xffffffff

# --- bibliothèque C ----------------------------------------------------
lib = ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'libmhfu.so'))
lib.psp_0885973c_ceil_log2_cap11.argtypes = [ctypes.c_uint32]; lib.psp_0885973c_ceil_log2_cap11.restype = ctypes.c_int32
lib.psp_0884fa40_clear_flag_bit.argtypes = [ctypes.c_void_p, ctypes.c_int32]; lib.psp_0884fa40_clear_flag_bit.restype = None
lib.psp_088e12fc_find_slot_by_id.argtypes = [ctypes.c_void_p, ctypes.c_uint32]; lib.psp_088e12fc_find_slot_by_id.restype = ctypes.c_int32
for n in ('psp_0888decc_dot3', 'psp_0888defc_dot_xz'):
    getattr(lib, n).argtypes = [ctypes.c_void_p, ctypes.c_void_p]; getattr(lib, n).restype = ctypes.c_float

rng = random.Random(0xA11E6A0)      # graine fixe : résultats reproductibles
gold = {}
report = []

def f2bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]

# ============ F1 : sub_0885973c ============
m = Machine(0x0885973c); ok = 0; recs = []
inputs = list(range(0, 5000)) + [1<<k for k in range(32)] + [(1<<k)+d for k in range(1,32) for d in (-1,1)] + [0xffffffff, 0x7fffffff, 0x80000000] + [rng.getrandbits(32) for _ in range(20000)]
inputs = [x & 0xffffffff for x in inputs]
for n in inputs:
    mu = m.call(a1=n); want = m.v0(mu); got = lib.psp_0885973c_ceil_log2_cap11(n)
    assert got == want, f"F1 diverge n={n}: orig={want} C={got}"
    ok += 1; recs.append(struct.pack('<Ii', n, want))
gold['0885973c'] = recs; report.append(('sub_0885973c ceil_log2_cap11', ok, len(m.code)//4))

# ============ F2 : sub_0884fa40 (écrit en mémoire) ============
m = Machine(0x0884fa40); ok = 0; recs = []
WIN = 16; WOFF = 0x500                       # fenêtre de 16 u16 à obj+0x500 (couvre idx -4..11)
bits = list(range(-64, 192)) * 8 + [rng.randint(-64, 191) for _ in range(5000)]
for bit in bits:
    win = bytes(rng.getrandbits(8) for _ in range(WIN*2))
    mu = m.call(a0=DATA_BASE, a1=bit & 0xffffffff, mem=[(DATA_BASE+WOFF, win)])
    want = bytes(mu.mem_read(DATA_BASE+WOFF, WIN*2))
    buf = (ctypes.c_uint8 * 0x1000)(); ctypes.memmove(ctypes.addressof(buf)+WOFF, win, WIN*2)
    lib.psp_0884fa40_clear_flag_bit(ctypes.addressof(buf), bit)
    got = bytes(buf[WOFF:WOFF+WIN*2])
    assert got == want, f"F2 diverge bit={bit}\n orig={want.hex()}\n   C ={got.hex()}"
    ok += 1; recs.append(struct.pack('<i', bit) + win + want)
gold['0884fa40'] = recs; report.append(('sub_0884fa40 clear_flag_bit', ok, len(m.code)//4))

# ============ F3 : sub_088e12fc (22 enregistrements de 12 octets à +0xFC) ============
m = Machine(0x088e12fc); ok = 0; hits = 0; recs = []
RECSZ, NREC, ROFF = 12, 22, 0xFC
pool = [rng.getrandbits(16) for _ in range(6)]
for t in range(6000):
    win = bytearray(rng.getrandbits(8) for _ in range(RECSZ*NREC))
    for r in range(NREC):                      # rend les correspondances fréquentes et les cas limites variés
        if rng.random() < 0.5: win[r*12+0] = (win[r*12+0] | 2) if rng.random() < 0.6 else (win[r*12+0] & ~2)
        if rng.random() < 0.7: win[r*12+2:r*12+4] = struct.pack('<H', rng.choice(pool))
    idv = rng.choice(pool) | (rng.getrandbits(16) << 16 if rng.random() < 0.3 else 0)   # bits hauts ignorés (id & 0xFFFF)
    mu = m.call(a0=DATA_BASE, a1=idv, mem=[(DATA_BASE+ROFF, bytes(win))])
    want = m.v0(mu)
    buf = (ctypes.c_uint8 * 0x1000)(); ctypes.memmove(ctypes.addressof(buf)+ROFF, bytes(win), len(win))
    got = lib.psp_088e12fc_find_slot_by_id(ctypes.addressof(buf), idv)
    assert got == want, f"F3 diverge id={idv:#x}: orig={want} C={got}"
    hits += (want != -1); ok += 1; recs.append(struct.pack('<I', idv) + bytes(win) + struct.pack('<i', want))
gold['088e12fc'] = recs; report.append((f'sub_088e12fc find_slot_by_id ({hits} trouvés / {ok-hits} échecs)', ok, len(m.code)//4))

# ============ F4/F5 : produits scalaires flottants (comparaison BIT À BIT) ============
def rnd_float():
    c = rng.random()
    if c < 0.45: return rng.uniform(-1000, 1000)
    if c < 0.60: return rng.uniform(-1e12, 1e12)
    if c < 0.75: return rng.choice([-1,1]) * 10 ** rng.uniform(-12, -15)       # petites valeurs NORMALES (pas de dénormaux)
    if c < 0.90: return float(rng.randint(-50, 50))
    return rng.choice([0.0, -0.0, 1.0, -1.0, 0.5, 1/3])
def tof32(x): return struct.unpack('<f', struct.pack('<f', x))[0]
for va, name, fn in ((0x0888decc, 'dot3', lib.psp_0888decc_dot3), (0x0888defc, 'dot_xz', lib.psp_0888defc_dot_xz)):
    m = Machine(va); ok = 0; recs = []
    for t in range(8000):
        A = [tof32(rnd_float()) for _ in range(3)]; B = [tof32(rnd_float()) for _ in range(3)]
        pa, pb = struct.pack('<3f', *A), struct.pack('<3f', *B)
        mu = m.call(a1=DATA_BASE, a2=DATA_BASE+0x100, mem=[(DATA_BASE, pa), (DATA_BASE+0x100, pb)])
        want = m.f0bits(mu)
        ca = (ctypes.c_float*3)(*A); cb = (ctypes.c_float*3)(*B)
        got = f2bits(fn(ctypes.addressof(ca), ctypes.addressof(cb)))
        assert got == want, f"{name} diverge A={A} B={B}: orig={want:#010x} C={got:#010x}"
        ok += 1; recs.append(pa + pb + struct.pack('<I', want))
    gold[f'{va:08x}'] = recs; report.append((f'sub_{va:08x} {name} (bit-exact)', ok, len(m.code)//4))

# --- rapport + fichier d'or ---
print("TEST DIFFÉRENTIEL : octets MIPS originaux (Unicorn) vs C (GCC)\n" + "-"*70)
for name, ok, nw in report: print(f"  OK  {name:58s} {ok:6d} vecteurs  ({nw} instr. MIPS)")
print("-"*70 + f"\nTOTAL : {sum(r[1] for r in report)} vecteurs, 0 divergence")
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden.bin'), 'wb') as f:
    f.write(b'MHFUGOLD1\0\0\0')
    for tag in ('0885973c','0884fa40','088e12fc','0888decc','0888defc'):
        f.write(bytes.fromhex(tag)); f.write(struct.pack('<I', len(gold[tag])))
        for r in gold[tag]: f.write(r)
print("golden.bin écrit :", os.path.getsize(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden.bin')), "octets")
