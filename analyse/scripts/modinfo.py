from elfmem import *
import hashlib, struct
MI = 0x0890c4a4
attr, = struct.unpack('<H', rd(MI,2)); ver = rd(MI+2,2)
name = rd(MI+4,27).split(b'\0')[0].decode()
gp, ent_top, ent_end, stub_top, stub_end = struct.unpack('<5I', rd(MI+0x20,20))
print(f"SceModuleInfo @0x{MI:08x}: attr=0x{attr:04x} ver={ver[1]}.{ver[0]} name='{name}'")
print(f"  gp=0x{gp:08x} ent=[0x{ent_top:08x},0x{ent_end:08x}) stub=[0x{stub_top:08x},0x{stub_end:08x})")

# --- imports
print("\n== Bibliotheques importees (PspModuleImport) ==")
p = stub_top; total=0; libs=[]
while p < stub_end:
    nameptr, ver, at, entLen, varCnt, fnCnt, nids, funcs = struct.unpack('<IHHBBHII', rd(p,20))
    ln = cstr(nameptr) if nameptr else '?'
    libs.append((ln, fnCnt, nids, funcs))
    total += fnCnt
    print(f"  {ln:22s} funcs={fnCnt:3d} vars={varCnt} nids@0x{nids:08x} stubs@0x{funcs:08x}")
    p += entLen*4
print(f"Total fonctions importees = {total}")

# --- exports
print("\n== Exports ==")
p = ent_top
while p < ent_end:
    nameptr, ver, at, entLen, varCnt, fnCnt, exps = struct.unpack('<IHHBBHI', rd(p,16))
    print(f"  lib={'(module)' if not nameptr else cstr(nameptr)} funcs={fnCnt} vars={varCnt} @0x{exps:08x}")
    p += entLen*4

import json
json.dump(libs, open('imports.json','w'))
