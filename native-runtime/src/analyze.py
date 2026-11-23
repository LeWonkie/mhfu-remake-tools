"""Read-only DATA.BIN / ELF32-LE audit. Python 3.10+, standard library only.

TOC detection is a structural hypothesis, never an identification of a game build.
No vendor parser, previous manifest, filename database or emulator is required.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import struct
import zlib

SECTOR = 2048

def sha(b):
    return hashlib.sha256(b).hexdigest()

def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def u32(b, off=0):
    return struct.unpack_from('<I', b, off)[0]

def entropy(b):
    if not b:
        return 0
    return round(-sum((n/len(b))*math.log2(n/len(b)) for n in collections.Counter(b).values()), 5)

def toc(path):
    size = path.stat().st_size
    with path.open('rb') as f:
        first = f.read(4)
        if len(first) != 4:
            raise ValueError('Short header')
        table_size = u32(first)*SECTOR
        if not 8 <= table_size <= min(size, 16*1024*1024):
            raise ValueError('No supported sector TOC at offset zero')
        f.seek(0)
        header = f.read(table_size)
    words = struct.unpack('<' + 'I'*(len(header)//4), header)
    k = 1
    while k < len(words) and words[k] >= words[k-1]:
        k += 1
    if k == len(words) or k < 3:
        raise ValueError('Ambiguous TOC: monotone offsets not followed by size pairs')
    count = k-1
    offsets = [v*SECTOR for v in words[:k]]
    if offsets[0] != table_size or offsets[-1] < table_size:
        raise ValueError('Inconsistent TOC extent')
    lengths = [offsets[i+1]-offsets[i] for i in range(count)]
    overrides = {}
    p = k
    while p+1 < len(words) and 0 < words[p] < count:
        index, length = words[p:p+2]
        if index >= count or index in overrides or length > lengths[index]:
            raise ValueError(f'Invalid size pair at table byte {p*4:#x}')
        overrides[index] = length
        p += 2
    tail=header[p*4:]
    rows = []
    for i, allocated in enumerate(lengths):
        length = overrides.get(i, allocated)
        start = offsets[i]
        available = max(0, min(length, size-start))
        rows.append(dict(index=i, offset=start, offset_hex=hex(start), sector=words[i],
                         allocated=allocated, length=length, explicit_length=i in overrides,
                         available=available, complete=available == length))
    meta = dict(input=str(path.resolve()), bytes=size, table_bytes=table_size,
                table_sha256=sha(header), entries=count, archive_end=offsets[-1],
                archive_end_hex=hex(offsets[-1]), size_pairs=len(overrides),
                offsets_end_byte=k*4, pairs_end_byte=p*4,
                table_tail_bytes=len(tail),table_tail_nonzero=sum(x!=0 for x in tail),
                table_tail_sha256=sha(tail),table_tail_entropy=entropy(tail),
                archive_complete=size >= offsets[-1], trailing_bytes=max(0,size-offsets[-1]),
                detection='candidate monotone LE sector offsets, followed by validated index/size pairs')
    return meta, rows

def classify(b):
    signatures = [(b'MWo3','MWo3'),(b'\x7fELF','ELF'),(b'~PSP','PSP container'),
                  (b'~SCE','SCE container candidate'),(b'\x00PBP','PBP'),
                  (b'\x00PSMF','PSMF'),(b'PSMF','PSMF'),(b'MIG.00.1PSP','GIM'),
                  (b'.TMH','TMH'),(b'TMH','TMH'),(b'RIFF','RIFF'),
                  (b'\x89PNG\r\n\x1a\n','PNG'),(b'\x1f\x8b','gzip candidate')]
    for magic, name in signatures:
        if b.startswith(magic):
            return name
    if not b:
        return 'empty'
    if not any(b):
        return 'zero'
    return 'unknown'

def overlay(b):
    if len(b)<128 or b[:4] != b'MWo3':
        return None
    magic, oid, load, text, data, bss, cs, ce = struct.unpack_from('<4s7I',b)
    # Measured on this corpus: text field INCLUDES the 64 zero bytes at 0x40.
    # Executable instructions begin at 0x80, data begins at 0x40+text.
    extent = 64+text+data
    code_bytes=min(max(0,text-64),max(0,len(b)-128))//4*4
    words = struct.unpack('<'+'I'*(code_bytes//4), b[128:128+code_bytes])
    # These instruction shapes are evidence of code, not a full MIPS decoder.
    return dict(id=oid, load=hex(load), text=text, data=data, bss=bss,
                field_18=hex(cs), field_1c=hex(ce),
                name=b[32:64].split(b'\0',1)[0].decode('ascii','replace'),
                code_file_offset=128,data_file_offset=64+text,
                zero_prefix=b[64:128]==bytes(64),
                expected_file_extent=extent, fits=extent<=len(b),
                tail_bytes=len(b)-extent, jal_words=sum(w>>26 == 3 for w in words),
                jr_ra_words=words.count(0x03e00008),
                stack_prologues=sum(w&0xffff0000 == 0x27bd0000 and w&0x8000 != 0 for w in words))

def compression(b):
    """Only validate wrapper candidates at entry start; never claim entropy=codec."""
    out=[]
    modes=[]
    if b.startswith(b'\x1f\x8b'):
        modes.append(('gzip',31))
    if len(b)>1 and b[0]&15 == 8 and b[0]>>4<=7 and (b[0]*256+b[1])%31 == 0:
        modes.append(('zlib',15))
    for name, mode in modes:
        try:
            dec=zlib.decompressobj(mode)
            raw=dec.decompress(b,64*1024*1024)
            out.append(dict(codec=name, eof=dec.eof, output_bytes=len(raw),
                            consumed=len(b)-len(dec.unused_data)-len(dec.unconsumed_tail),
                            output_sha256=sha(raw) if dec.eof else None,
                            status='validated' if dec.eof else 'limit or incomplete'))
        except zlib.error as e:
            out.append(dict(codec=name,status='rejected',error=str(e)))
    return out

def data_audit(path, out, prefix=None):
    meta, rows=toc(path)
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):
            digest.update(chunk)
    meta['sha256']=digest.hexdigest()
    overlays=[];riff=[]
    with path.open('rb') as f:
        for row in rows:
            f.seek(row['offset'])
            b=f.read(row['available'])
            row.update(magic16=b[:16].hex(),kind=classify(b),
                       sha256=sha(b) if row['complete'] else '',
                       prefix_entropy=entropy(b[:65536]))
            comp=compression(b)
            if comp:
                row['compression']=json.dumps(comp)
            ov=overlay(b)
            if ov:
                overlays.append(dict(index=row['index'], archive_offset=row['offset'],**ov))
            if b.startswith(b'RIFF') and len(b)>=12:
                declared=u32(b,4)+8;pos=12;chunks=[]
                while pos+8<=min(declared,len(b)):
                    length=u32(b,pos+4)
                    chunk=dict(id=b[pos:pos+4].decode('ascii','replace'),offset=pos,bytes=length,fits=pos+8+length<=len(b))
                    if b[pos:pos+4]==b'fmt ' and length>=16 and pos+24<=len(b):
                        tag,channels,rate=struct.unpack_from('<HHI',b,pos+8)
                        chunk.update(format_tag=hex(tag),channels=channels,sample_rate=rate)
                    chunks.append(chunk)
                    if not chunk['fits']:break
                    pos+=8+length+(length&1)
                riff.append(dict(index=row['index'],form=b[8:12].decode('ascii','replace'),declared_bytes=declared,fits=declared<=len(b),chunks=chunks))
            pad_len=max(0,min(row['allocated']-row['length'],path.stat().st_size-row['offset']-row['length']))
            pad=f.read(pad_len) if row['complete'] else b''
            row['padding_observed']=len(pad)
            row['padding_nonzero']=sum(v!=0 for v in pad)
    meta.update(complete_entries=sum(x['complete'] for x in rows),
                partial_entries=sum(0<x['available']<x['length'] for x in rows),
                absent_entries=sum(x['available']==0 and x['length']>0 for x in rows),
                kinds=dict(collections.Counter(x['kind'] for x in rows)),
                overlays=len(overlays),zero_allocations=sum(x['allocated']==0 for x in rows),
                padding_nonzero=sum(x['padding_nonzero'] for x in rows))
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with (out/'entries.csv').open('w',newline='',encoding='utf-8') as f:
        wr=csv.DictWriter(f,keys);wr.writeheader();wr.writerows(rows)
    dump(out/'data.json',meta)
    dump(out/'overlays.json',overlays)
    dump(out/'riff.json',riff)
    if prefix:
        # Evaluate prefix coverage without creating or misidentifying another input file.
        dump(out/'prefix_coverage.json',dict(simulated=True,source_sha256=meta['sha256'],bytes=prefix,
             complete=sum(r['offset']+r['length']<=prefix for r in rows),
             partial=[dict(index=r['index'],offset=r['offset'],length=r['length'],available=prefix-r['offset'],complete=False) for r in rows if r['offset']<prefix<r['offset']+r['length']],
             absent=sum(r['offset']>=prefix for r in rows)))
    return meta

REG=['zero','at','v0','v1','a0','a1','a2','a3','t0','t1','t2','t3','t4','t5','t6','t7','s0','s1','s2','s3','s4','s5','s6','s7','t8','t9','k0','k1','gp','sp','fp','ra']

def instruction(w,pc):
    op=w>>26;rs=REG[(w>>21)&31];rt=REG[(w>>16)&31];rd=REG[(w>>11)&31]
    imm=w&65535;simm=imm if imm<32768 else imm-65536
    if not w:return 'nop'
    if op in (2,3):return ('jal' if op==3 else 'j')+' '+hex(((pc+4)&0xf0000000)|((w&0x3ffffff)<<2))
    if op==0:
        fn=w&63
        if fn==8:return 'jr '+rs
        if fn==9:return f'jalr {rd},{rs}'
        if fn in (0,2,3):return f'{ {0:"sll",2:"srl",3:"sra"}[fn]} {rd},{rt},{(w>>6)&31}'
        if fn in (0x21,0x23,0x24,0x25,0x26,0x27,0x2a,0x2b):return f'{ {0x21:"addu",0x23:"subu",0x24:"and",0x25:"or",0x26:"xor",0x27:"nor",0x2a:"slt",0x2b:"sltu"}[fn]} {rd},{rs},{rt}'
    if op==15:return f'lui {rt},{imm:#x}'
    if op==31 and w&63==32 and ((w>>6)&31) in (16,24):return f'{"seb" if ((w>>6)&31)==16 else "seh"} {rd},{rt}'
    if op in (9,12,13,14):return f'{ {9:"addiu",12:"andi",13:"ori",14:"xori"}[op]} {rt},{rs},{simm if op==9 else hex(imm)}'
    if op in (10,11):return f'{"slti" if op==10 else "sltiu"} {rt},{rs},{simm}'
    if op in (4,5,20,21):return f'{ {4:"beq",5:"bne",20:"beql",21:"bnel"}[op]} {rs},{rt},{pc+4+simm*4:#x}'
    if op in (6,7,22,23):return f'{ {6:"blez",7:"bgtz",22:"blezl",23:"bgtzl"}[op]} {rs},{pc+4+simm*4:#x}'
    if op==1 and (w>>16)&31 in (0,1,2,3):return f'{ {0:"bltz",1:"bgez",2:"bltzl",3:"bgezl"}[(w>>16)&31]} {rs},{pc+4+simm*4:#x}'
    if op in (0x20,0x21,0x23,0x24,0x25,0x28,0x29,0x2b):return f'{ {0x20:"lb",0x21:"lh",0x23:"lw",0x24:"lbu",0x25:"lhu",0x28:"sb",0x29:"sh",0x2b:"sw"}[op]} {rt},{simm}({rs})'
    return f'.word {w:#010x} ; decoder subset'

def elf_audit(path,out):
    b=path.read_bytes()
    result=dict(input=str(path.resolve()),bytes=len(b),sha256=sha(b),magic=b[:16].hex())
    if b[:6] != b'\x7fELF\x01\x01':
        result['nonzero_bytes']=sum(x!=0 for x in b)
        result['status']='unsupported: need plain ELF32 little-endian; no decryption attempted'
        dump(out/(path.name+'.json'),result);return result
    et,em,ver,entry,phoff,shoff,flags,ehsz,phsz,phn,shsz,shn,shstr=struct.unpack_from('<HH5I6H',b,16)
    sections=[]
    for i in range(shn):
        vals=struct.unpack_from('<10I',b,shoff+i*shsz)
        sections.append(dict(zip(['name_offset','type','flags','address','offset','size','link','info','alignment','entsize'],vals)))
    ns=sections[shstr];names=b[ns['offset']:ns['offset']+ns['size']]
    for s in sections:
        s['name']=names[s['name_offset']:].split(b'\0',1)[0].decode('ascii','replace')
    programs=[]
    for i in range(phn):
        vals=struct.unpack_from('<8I',b,phoff+i*phsz)
        programs.append(dict(zip(['type','offset','vaddr','paddr','filesz','memsz','flags','align'],vals)))
    def va(addr,n=1):
        for p in programs:
            if p['type']==1 and p['vaddr']<=addr and addr+n<=p['vaddr']+p['filesz']:
                return p['offset']+addr-p['vaddr']
        raise ValueError(f'VA not file backed: {addr:#x}, size {n}')
    def strva(addr):
        off=va(addr);return b[off:off+256].split(b'\0',1)[0].decode('ascii','replace')
    result.update(type=et,machine=em,entry=hex(entry),flags=hex(flags),programs=programs,sections=sections)
    if em!=8:
        raise ValueError('Not MIPS')
    mi=next((s for s in sections if 'sceModuleInfo' in s['name']),None)
    libs=[];imports=[];exports=[]
    if mi:
        off=mi['offset'];attr,version=struct.unpack_from('<HH',b,off)
        gp,ent,entend,stub,stubend=struct.unpack_from('<5I',b,off+32)
        result['module']=dict(name=b[off+4:off+32].split(b'\0',1)[0].decode('ascii','replace'),attributes=attr,version=version,
                              gp=hex(gp),export_start=hex(ent),export_end=hex(entend),import_start=hex(stub),import_end=hex(stubend))
        cursor=stub
        while cursor<stubend:
            off=va(cursor,20)
            name,version,attr,lwords,nvar,nfunc,nids,stubs=struct.unpack_from('<IHHBBHII',b,off)
            if lwords<5 or cursor+lwords*4>stubend:raise ValueError('Invalid import descriptor')
            lib=strva(name)
            libs.append(dict(library=lib,descriptor_va=hex(cursor),version=version,attributes=attr,words=lwords,functions=nfunc,variables=nvar))
            for i in range(nfunc):
                nid=u32(b,va(nids+i*4,4));sv=stubs+i*8
                imports.append(dict(library=lib,nid=f'0x{nid:08X}',stub_va=hex(sv),stub_offset=va(sv,8),stub_bytes=b[va(sv,8):va(sv,8)+8].hex()))
            cursor+=lwords*4
        cursor=ent
        while cursor<entend:
            off=va(cursor,16)
            name,version,attr,lwords,nvar,nfunc,table=struct.unpack_from('<IHHBBHI',b,off)
            if lwords<4 or cursor+lwords*4>entend:raise ValueError('Invalid export descriptor')
            count=nvar+nfunc
            for i in range(count):
                nid=u32(b,va(table+i*4,4));value=u32(b,va(table+(count+i)*4,4))
                exports.append(dict(library=strva(name) if name else '(system)',nid=f'0x{nid:08X}',value=hex(value),kind='function' if i<nfunc else 'variable'))
            cursor+=lwords*4
    names_path=Path(__file__).with_name('nid_names.json')
    name_map={}
    if names_path.exists():
        name_map={(x['library'],x['nid']):x['name'] for x in json.loads(names_path.read_text(encoding='utf-8'))['mappings']}
    for x in imports:
        x['name']=name_map.get((x['library'],x['nid']))
        if x['name']:
            x['name_sha1_matches_nid']=int.from_bytes(hashlib.sha1(x['name'].encode()).digest()[:4],'little')==int(x['nid'],16)
    lookup={int(x['stub_va'],16):x for x in imports};calls=[]
    for s in sections:
        if s['type']!=1 or not s['flags']&4:continue
        for o in range(0,s['size']-3,4):
            w=u32(b,s['offset']+o);pc=s['address']+o
            if w>>26 in (2,3):
                target=((pc+4)&0xf0000000)|((w&0x3ffffff)<<2)
                if target in lookup:
                    x=lookup[target];calls.append(dict(pc=hex(pc),offset=s['offset']+o,opcode='jal' if w>>26==3 else 'j',library=x['library'],nid=x['nid'],name=x['name'],stub_va=x['stub_va']))
    def disasm(start,words):
        lines=[]
        for i in range(words):
            pc=start+i*4
            try:off=va(pc,4)
            except ValueError:break
            w=u32(b,off);label=''
            if w>>26 in (2,3):
                target=((pc+4)&0xf0000000)|((w&0x3ffffff)<<2)
                if target in lookup:label=' ; '+(lookup[target]['name'] or lookup[target]['library']+' '+lookup[target]['nid'])
            lines.append(f'{pc:08X} file+{off:08X}  {w:08X}  {instruction(w,pc)}{label}')
        return lines
    startup=disasm(entry,128)
    targets=[]
    for e in exports:
        if e['nid']=='0xD632ACDB':targets.append(int(e['value'],16))
    result.update(libraries=libs,imports=imports,exports=exports,direct_import_calls=calls,
                  startup_export_candidates=[hex(x) for x in targets])
    if imports:
        with (out/(path.name+'.imports.csv')).open('w',encoding='utf-8',newline='') as f:
            keys=list(dict.fromkeys(k for row in imports for k in row))
            writer=csv.DictWriter(f,keys);writer.writeheader();writer.writerows(imports)
    for target in sorted(set(targets)):
        if target!=entry:startup+=['',f'Export module_start candidate {target:#x}:']+disasm(target,128)
    (out/(path.name+'.startup.txt')).write_text('\n'.join(startup)+'\n',encoding='utf-8')
    dump(out/(path.name+'.json'),result)
    return result

def extract(path,index,destination,allocated=False):
    if path.resolve() == destination.resolve():
        raise ValueError('Source and destination must differ')
    meta,rows=toc(path)
    if index<0 or index>=len(rows):raise ValueError('Index out of range')
    row=rows[index]
    length=row['allocated'] if allocated else row['length']
    if row['offset']+length>path.stat().st_size:raise ValueError('Entry incomplete or absent in this prefix; refusing partial extraction')
    with path.open('rb') as f:
        f.seek(row['offset']);b=f.read(length)
    if len(b) != length:
        raise ValueError('Short read; refusing partial extraction')
    with destination.open('xb') as f:f.write(b)
    if destination.read_bytes()!=b:raise ValueError('Extraction verification failed')
    return dict(**row,output=str(destination.resolve()),extracted_bytes=length,mode='allocated' if allocated else 'table-length',sha256=sha(b),verified=True)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('audit');p.add_argument('--data',type=Path);p.add_argument('--elf',type=Path,nargs='*',default=[]);p.add_argument('--out',type=Path,required=True);p.add_argument('--simulate-prefix',type=int)
    p=sub.add_parser('extract');p.add_argument('data',type=Path);p.add_argument('index',type=int);p.add_argument('destination',type=Path);p.add_argument('--allocated',action='store_true',help='Preserve the full sector allocation, including nonzero tail bytes')
    a=ap.parse_args()
    if a.cmd=='extract':print(json.dumps(extract(a.data,a.index,a.destination,a.allocated),indent=2));return
    a.out.mkdir(parents=True,exist_ok=True)
    if a.data:print(json.dumps(data_audit(a.data,a.out,a.simulate_prefix),indent=2))
    for p in a.elf:
        r=elf_audit(p,a.out);print(json.dumps({k:v for k,v in r.items() if k not in ('sections','programs','imports','direct_import_calls')},indent=2))

if __name__=='__main__':main()
