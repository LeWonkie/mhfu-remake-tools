"""Table (bibliothèque, NID) -> nom, extraite des .S du pspsdk (IMPORT_FUNC "Lib",0xNID,nom).
Contrôle croisé : pour les noms non obfusqués, NID == SHA1(nom)[0:4] (little-endian)."""
import re, glob, hashlib, json, struct
tab = {}
pat = re.compile(r'IMPORT_FUNC\s+"(\w+)"\s*,\s*(0x[0-9A-Fa-f]+)\s*,\s*(\w+)')
for f in glob.glob('pspsdk/src/**/*.S', recursive=True):
    for m in pat.finditer(open(f, errors='ignore').read()):
        tab.setdefault((m.group(1), int(m.group(2),16)), set()).add(m.group(3))
def sha_nid(n): return struct.unpack('<I', hashlib.sha1(n.encode()).digest()[:4])[0]
ok = sum(1 for (l,n),ns in tab.items() if any(sha_nid(x)==n for x in ns))
print(f"couples (lib,NID) connus : {len(tab)} | cohérents avec SHA1(nom) : {ok} ({ok/len(tab)*100:.1f} %)")
json.dump({f"{l}:{n:08x}":sorted(v) for (l,n),v in tab.items()}, open('nid_names.json','w'))
