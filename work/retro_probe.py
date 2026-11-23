import sys, json, struct, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_core as core, atelier_campaign as campaign, atelier_pilot as ap, atelier_zones as z
import probe_village_npcs as npc

w = core.Workshop(ROOT)
seed = '20261020-153034-ceda7b'
protected = [ROOT/'game-mhfu/ms0', w.data_dir/'seeds'/seed/'ms0']
before = [core._tree_digest(p) for p in protected]
cancel = threading.Event()
timer = threading.Timer(100, cancel.set)
timer.start()
s = None
result = {'before': before, 'frames': []}
try:
    scenario = {'name':'retro-probe', 'label':'Retro memory probe', 'fast_boot':True,
                'debug':True, 'script':campaign.BOOT, 'screenshot_every':0,
                'settings':{'video.fast_forward':'toggle','video.fast_forward_speed':'8','video.fast_loading':'1'}}
    began = time.monotonic()
    s = campaign.Session(w, seed, 115, variant=core.register_rtc_variant('normal',1609459200123456),scenario=scenario)
    s.cancel = cancel
    p = ap.Pilot(s, cancel=cancel)
    p.wait_village(60)
    result['boot_seconds'] = time.monotonic()-began
    p.ensure_fast_forward()
    result['npcs'] = npc.npcs(s)
    area = z.current(s)
    for i in range(23):
        p.check()
        if i == 1:
            target = next(n for n in result['npcs'] if n['kind'] == 2)
            x,y,zz=target['position']
            z.place(s,area['player'],(x+80,y,zz))
            p.pause(.6)
        if i:
            s.inputs(['0:pad b 6'])
            p.pause(.7)
        font=z.u32(s,0x08A5E278)
        raw=z.read(s,font,0x300)
        (s.dir/f'font-{i}.bin').write_bytes(raw)
        counts=list(raw[0x134:0x13a])
        ptrs=struct.unpack_from('<6I',raw,0x140)
        frame={'font':font,'counts':counts,'ptrs':ptrs,'scene':p.snapshot()['scene'],'runs':[]}
        for layer,(count,ptr) in enumerate(zip(counts,ptrs)):
            if not 0x08800000 <= ptr < 0x0a000000 or count>128: continue
            data=z.read(s,ptr,count*12)
            for n in range(count):
                x,y,fw,fh,color,spacing,textptr=struct.unpack_from('<hhBBbBI',data,n*12)
                if not 0x08800000<=textptr<0x09fffe00: continue
                text=z.read(s,textptr,256)
                codes=struct.unpack('<128H',text)
                out=[]
                for code in codes:
                    if code==0: break
                    out.append(chr(code))
                frame['runs'].append({'layer':layer,'x':x,'y':y,'text':''.join(out),'ptr':textptr})
        result['frames'].append(frame)
        p.confirm_shots([p.shoot(f'probe-{i}')])
        core._write_json(s.dir/'retro-probe.json',result)
    print(s.dir, flush=True)
except Exception as e:
    result['error']=repr(e)
    print(repr(e), flush=True)
finally:
    timer.cancel()
    w.stop()
    result['after']=[core._tree_digest(q) for q in protected]
    result['protected_unchanged']=before==result['after']
    if s: core._write_json(s.dir/'retro-probe.json', result)
    print(json.dumps(result,ensure_ascii=True),flush=True)
