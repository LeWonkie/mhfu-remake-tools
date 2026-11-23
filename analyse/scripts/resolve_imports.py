from elfmem import *
import json, struct
libs = json.load(open('imports.json'))
names = json.load(open('nid_names.json'))
stub_map = {}   # adresse du stub -> (lib, nid, nom|None)
res = unres = 0
per_lib = {}
for ln, fn, nids, funcs in libs:
    r = 0
    for i in range(fn):
        nid = u32(nids + 4*i)
        cand = names.get(f"{ln}:{nid:08x}")
        nm = cand[0] if cand else None
        stub_map[funcs + 8*i] = (ln, nid, nm)
        r += nm is not None
    per_lib[ln] = per_lib.get(ln, [0,0]); per_lib[ln][0]+=r; per_lib[ln][1]+=fn
    res += r; unres += fn - r
print(f"Imports resolus par NID : {res} / {res+unres}")
for ln,(r,t) in per_lib.items(): print(f"   {ln:22s} {r:3d}/{t:3d}")
json.dump({hex(k):v for k,v in stub_map.items()}, open('stub_map.json','w'))

first = sorted(stub_map)[0]
print("\nExemple de stub @0x%08x (octets bruts) : %s" % (first, rd(first, 8).hex()))
print("\nImports cles :")
keys = ('sceKernelCreateThread','sceGeListEnQueue','sceCtrlReadBufferPositive','sceDisplaySetFrameBuf',
        'sceIoOpen','sceAudioOutputBlocking','sceKernelExitGame','sceMpegCreate',
        'sceUtilitySavedataInitStart','sceNetAdhocPdpSend','sceSasCore','sceAtracDecodeData','sceDmacMemcpy')
for addr,(l,n,nm) in sorted(stub_map.items()):
    if nm and any(k in nm for k in keys):
        print("   0x%08x  %s::%s" % (addr, l, nm))
