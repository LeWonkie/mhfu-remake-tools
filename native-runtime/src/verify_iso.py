"""Read an explicitly supplied ISO; independently locate and hash its game files."""
import argparse
import hashlib
import json
from pathlib import Path
from investigate import iso_find

def main():
    ap=argparse.ArgumentParser();ap.add_argument('iso',type=Path);ap.add_argument('--results',type=Path,required=True)
    args=ap.parse_args();root=args.results
    digest=hashlib.sha256()
    with args.iso.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):digest.update(b)
        report=dict(iso=str(args.iso.resolve()),bytes=args.iso.stat().st_size,sha256=digest.hexdigest(),files=[])
        for path,reference in [('/PSP_GAME/USRDIR/DATA.BIN','data.json'),('/PSP_GAME/SYSDIR/EBOOT.BIN','EBOOT.BIN.json'),('/PSP_GAME/SYSDIR/BOOT.BIN','BOOT.BIN.json')]:
            loc=iso_find(f,path);f.seek(loc['extent']*2048);remaining=loc['size'];h=hashlib.sha256();prefix=hashlib.sha256();count=0;nonzero=0
            while remaining:
                b=f.read(min(remaining,4*1024*1024))
                if not b:raise ValueError('Truncated ISO file')
                h.update(b)
                if count<419430400:prefix.update(b[:419430400-count])
                if path.endswith('/BOOT.BIN'):nonzero+=sum(x!=0 for x in b)
                count+=len(b);remaining-=len(b)
            expected=json.loads((root/reference).read_text(encoding='utf-8'))
            row=dict(path=path,iso_byte_offset=loc['extent']*2048,bytes=loc['size'],sha256=h.hexdigest(),matches_audited_bytes=loc['size']==expected['bytes'] and h.hexdigest()==expected['sha256'])
            if path.endswith('/DATA.BIN'):row['first_400MiB_sha256']=prefix.hexdigest();row['prefix_bytes']=min(count,419430400)
            if path.endswith('/BOOT.BIN'):row['nonzero_bytes']=nonzero
            report['files'].append(row)
    report['all_three_match']=all(x['matches_audited_bytes'] for x in report['files'])
    (root/'supplied_iso_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
