"""Carte des overlays : on apparie la i-ème section *.ovl (hors .rel*) avec le PH n°(i+1).
Contrôle de cohérence : la taille maximale de chaque région doit retomber exactement sur l'adresse de la région suivante."""
import struct, collections
E = open('/mnt/user-data/uploads/EBOOT.BIN','rb').read()
(_,_,_,_,entry,phoff,shoff,flags,_,phes,phn,shes,shn,shstr) = struct.unpack('<16sHHIIIIIHHHHHH', E[:52])
ph = [struct.unpack('<8I', E[phoff+i*phes:phoff+i*phes+32]) for i in range(phn)]
sh = [struct.unpack('<10I', E[shoff+i*shes:shoff+i*shes+40]) for i in range(shn)]
so = sh[shstr][4]
nm = lambda n: E[so+n:E.index(b'\0',so+n)].decode('latin1')
ovl = [(nm(s[0]), s[3]) for s in sh if nm(s[0]).endswith('.ovl') and not nm(s[0]).startswith('.rel')]       # 298 : 'xxx.ovl'
assert len(ovl) == 298 and phn == 300, (len(ovl), phn)
# 298 noms .ovl (sans .rel) + 'debug.elf' => 299 PH de type NULL
names = [n for n,_ in ovl] + ['debug.elf']
pairs = list(zip(names, ph[1:]))
regions = collections.OrderedDict()
for n,p in pairs:
    t,off,va,pa,fs,ms,fl,al = p
    regions.setdefault(va, []).append((n, ms, fs))
print(f"{'region VMA':>12s} {'nb ovl':>6s} {'taille max':>11s} {'fin région':>11s}   plus gros overlay")
keys = list(regions)
for i,va in enumerate(keys):
    L = regions[va]; mx = max(L, key=lambda x:x[1])
    end = va + mx[1]
    nxt = keys[i+1] if i+1 < len(keys) else None
    chk = '' if nxt is None else ('== début région suivante OK' if end == nxt else f'!= suivante 0x{nxt:08x}')
    print(f"  0x{va:08x} {len(L):6d} {mx[1]:11,d}  0x{end:08x}   {mx[0]} {chk}")
print("\nfilesz non nul pour un overlay ? ->", any(p[4] for _,p in pairs), " (si False : AUCUN octet d'overlay dans ce fichier)")
print("\nTâches (task) et sous-tâches :")
for n,p in pairs:
    if n.endswith(('_task.ovl','_sub.ovl')): print(f"   {n:22s} memsz={p[5]:>10,d} octets")
em = [(n,p[5]) for n,p in pairs if n.startswith('em')]
print("\nOverlays ennemis (em*.ovl) :", ', '.join(f"{n[:-4]}:{s//1024}Ko" for n,s in em))
