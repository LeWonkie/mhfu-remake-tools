import sys, json, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'outils/atelier-mhfu'))
import atelier_core as c, atelier_retro as r, atelier_pilot as a, atelier_campaign as b, atelier_zones as z
w=c.Workshop(ROOT)
assert not r.yakumo_pids()
paths=[ROOT/'game-mhfu/ms0',w.data_dir/'seeds/20261020-153034-ceda7b/ms0']
before=[c._tree_digest(p) for p in paths]
cancel=threading.Event(); timer=threading.Timer(90,cancel.set);timer.start()
s=None
try:
 s=b.Session(w,'20261020-153034-ceda7b',100,variant=c.register_rtc_variant('normal',1609459200123456),scenario={'name':'menu-probe','label':'Menu observation','fast_boot':True,'debug':True,'script':b.BOOT,'screenshot_every':0,'settings':{'video.fast_forward':'toggle','video.fast_forward_speed':'8','video.fast_loading':'1'}})
 s.cancel=cancel;p=a.Pilot(s,cancel);p.wait_village(60);p.ensure_fast_forward();rp=r.RetroPilot(p,time.monotonic()+60)
 rp.command('ui on')
 npcs=rp.json_command('npc','npc'); target=next(n for n in npcs if n['type']==2)
 rp.command(f'talk {target["slot"]}');p.pause(.8)
 for i in range(30):
  if any(x['text']=='Weaponcraft' for x in rp.ui()['runs']):break
  rp.button('b')
 p.confirm_shots([p.shoot('menu-before')]);print(s.dir,flush=True)
 rp.command('trace 0x0882F7C4 8');p.pause(.3)
 rp.command('trace 0x0882FBEC 8');p.pause(.3)
 rp.command('trace 0x0882F6E8 8');p.pause(.3)
 rp.command(f'dump "{s.dir / "before.bin"}" 0x08800000 0x01800000',15)
 c._write_json(s.dir/'before-ui.json',rp.ui())
 rp.button('dpdown');p.confirm_shots([p.shoot('menu-after')])
 rp.command(f'dump "{s.dir / "after.bin"}" 0x08800000 0x01800000',15)
 c._write_json(s.dir/'after-ui.json',rp.ui())
 states=[]
 for i in range(3):
  for button in ['dpup','dpdown','dpup','b']:
   states.append({'i':i,'button':button,'state':z.read(s,0x09B0AC38,0x100).hex(),'ui':rp.ui()})
   rp.button(button)
 c._write_json(s.dir/'lobby-states.json',states)
finally:
 timer.cancel();w.stop();print('protected',before==[c._tree_digest(p) for p in paths]);print('pids',r.yakumo_pids())
