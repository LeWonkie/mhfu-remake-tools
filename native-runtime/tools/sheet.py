# usage: sheet.py <run> <out.png> <from_s> <to_s> <step_s> [cols]
import sys
from PIL import Image, ImageDraw
from pathlib import Path
run,out,a,b,step=sys.argv[1],sys.argv[2],*map(int,sys.argv[3:6]);cols=int(sys.argv[6]) if len(sys.argv)>6 else 6
fr={int(f.name.split('_')[1][:3]):f for f in Path(run,'frames').glob('*.bmp')}
pick=[(s,fr[s]) for s in range(a,b+1,step) if s in fr]
W,H=240,136;rows=(len(pick)+cols-1)//cols
sheet=Image.new('RGB',(W*cols,(H+14)*rows),'white');d=ImageDraw.Draw(sheet)
for i,(s,f) in enumerate(pick):
    x,y=(i%cols)*W,(i//cols)*(H+14);sheet.paste(Image.open(f).convert('RGB').resize((W,H)),(x,y+14));d.text((x+2,y+1),f'{s}s',fill='black')
sheet.save(out)
