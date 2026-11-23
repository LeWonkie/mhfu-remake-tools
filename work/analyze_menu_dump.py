from pathlib import Path
import struct,json
root=Path(__file__).resolve().parents[1]
p=root/'atelier-psprecomp/runs/20261027-163720-ab1534'
a=(p/'before.bin').read_bytes();b=(p/'after.bin').read_bytes();base=0x08800000
def u32(addr):return struct.unpack_from('<I',a,addr-base)[0]
regions={}
for name,addr,size in [('lobby',0x09B0AC38,0x100),('lobby_manager',u32(0x09B1D9E8),0x800),('npc_manager',u32(0x09B1E708),0x400),('cockpit',u32(0x089C6CBC),0x1220)]:
 regions[name]={'address':addr,'changes':[(hex(i),a[addr-base+i],b[addr-base+i]) for i in range(size) if a[addr-base+i]!=b[addr-base+i]]}
print(json.dumps(regions))
print('0->1 candidates (heap/lobby):')
found=[hex(base+i) for i,(x,y) in enumerate(zip(a,b)) if x==0 and y==1 and ((0x08b00000<=base+i<0x09a50000) or 0x09b00000<=base+i<0x09b20000)]
print(found[:200],len(found))
for s in ['Weaponcraft','Create Weapon']:
 needle=s.encode();offset=a.find(needle);print(s,hex(base+offset))
