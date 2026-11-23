# usage: vram2png.py out.png fmt vram1.bin [...]; fmt 565|5551|4444|8888; buffers at 0 and one frame later
import sys
from PIL import Image, ImageDraw
from pathlib import Path
import numpy as np
out,fmt,files=sys.argv[1],sys.argv[2],sys.argv[3:]
bpp=4 if fmt=='8888' else 2;size=512*272*bpp
def decode(raw):
    if fmt=='8888':return np.frombuffer(raw,np.uint8).reshape(272,512,4)[:,:480,:3]
    v=np.frombuffer(raw,'<u2').reshape(272,512)[:,:480].astype(np.uint32)
    if fmt=='565':r,g,b=(v&31)*255//31,((v>>5)&63)*255//63,((v>>11)&31)*255//31
    elif fmt=='5551':r,g,b=(v&31)*255//31,((v>>5)&31)*255//31,((v>>10)&31)*255//31
    else:r,g,b=(v&15)*17,((v>>4)&15)*17,((v>>8)&15)*17
    return np.stack([r,g,b],-1).astype(np.uint8)
W,H=240,136;sheet=Image.new('RGB',(W*2,(H+12)*len(files)),'white');d=ImageDraw.Draw(sheet)
for row,f in enumerate(files):
    raw=Path(f).read_bytes()
    for col in (0,1):
        sheet.paste(Image.fromarray(decode(raw[col*size:(col+1)*size])).resize((W,H)),(col*W,row*(H+12)+12))
    d.text((2,row*(H+12)),f"{Path(f).parent.name}/{Path(f).stem} {fmt}",fill='black')
sheet.save(out)
