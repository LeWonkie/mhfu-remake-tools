# usage: framecmp.py runA runB -> identical/different frame counts and RAM hash
import sys,re
from PIL import Image, ImageChops
from pathlib import Path
A,B=Path(sys.argv[1]),Path(sys.argv[2])
b={f.name.split('_')[0]:f for f in (B/'frames').glob('*.bmp')};same=diff=0;first=None
for f in sorted((A/'frames').glob('*.bmp')):
    g=b.get(f.name.split('_')[0])
    if not g:continue
    if ImageChops.difference(Image.open(f).convert('RGB'),Image.open(g).convert('RGB')).getbbox() is None:same+=1
    else:
        diff+=1;first=first or f.name[:7]
ram=lambda d:(re.search(r'"memory_sha256":"(\w{12})',(d/'advance-0.stdout.jsonl').read_text(errors='replace')[-30000:]) or [None,None])[1]
print(f'{A.name} vs {B.name}: identiques {same}, differentes {diff}, premiere difference {first}, RAM {ram(A)} / {ram(B)}')
