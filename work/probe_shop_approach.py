import sys,json,threading,time,math
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'outils/atelier-mhfu'))
import atelier_core as c, atelier_retro as r, atelier_pilot as a, atelier_campaign as b, atelier_zones as z
w=c.Workshop(root);assert not r.yakumo_pids();seed='20261020-153034-ceda7b'
paths=[root/'game-mhfu/ms0',w.data_dir/'seeds'/seed/'ms0'];before=[c._tree_digest(p) for p in paths]
cancel=threading.Event();timer=threading.Timer(90,cancel.set);timer.start();s=None;result=[]
try:
 s=b.Session(w,seed,100,variant=c.register_rtc_variant('normal',1609459200123456),scenario={'name':'shop-approach','fast_boot':True,'debug':True,'script':b.BOOT,'screenshot_every':0,'settings':{'video.fast_forward':'toggle','video.fast_forward_speed':'8','video.fast_loading':'1'}})
 s.cancel=cancel;p=a.Pilot(s,cancel);p.wait_village(60);p.ensure_fast_forward();rp=r.RetroPilot(p,time.monotonic()+60)
 npc=next(n for n in rp.json_command('npc','npc') if n['type']==1);area=z.current(s);x,y,zz=npc['position']
 for i,(dx,dz) in enumerate([(0,120),(0,-120),(-120,0),(80,0),(0,60),(0,-60),(-60,0),(60,60)]):
  z.place(s,area['player'],(x+dx,y,zz+dz));yaw=int(math.atan2(-dx,-dz)*65536/(2*math.pi))&65535
  rp.command(f'poke16 {area["player"]+0x1f4} {yaw}');rp.command(f'poke16 {area["player"]+0x320} {yaw}');p.pause(.5);rp.button('b');p.pause(.5)
  ui=rp.ui();result.append({'offset':[dx,dz],'ui':ui,'actual':z.current(s)['position']})
  p.confirm_shots([p.shoot(f'approach-{i}')])
  if ui['scene']!=0:
   try:rp.advance_dialogue_until('menu',20)
   except Exception as e:result.append({'error':str(e)})
   p.confirm_shots([p.shoot('shop-menu')]);result.append({'ui':rp.ui()});break
 c._write_json(s.dir/'shop-approach.json',result);r.report(s.dir,{'state':'probe'},rp.transcript);print(s.dir)
finally:
 timer.cancel();w.stop();print('protected',before==[c._tree_digest(p) for p in paths]);print('pids',r.yakumo_pids())
