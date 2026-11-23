import sys,time,threading,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'outils/atelier-mhfu'))
import atelier_core as c, atelier_retro as r, atelier_pilot as a, atelier_campaign as b, atelier_zones as z, probe_village_npcs as n
w=c.Workshop(root);assert not r.yakumo_pids();seed='20261020-153034-ceda7b';paths=[root/'game-mhfu/ms0',w.data_dir/'seeds'/seed/'ms0'];before=[c._tree_digest(p) for p in paths]
cancel=threading.Event();timer=threading.Timer(90,cancel.set);timer.start();s=None;results=[]
try:
 s=b.Session(w,seed,100,variant=c.register_rtc_variant('normal',1609459200123456),scenario={'name':'farm-standing','fast_boot':True,'debug':True,'script':b.BOOT,'screenshot_every':0,'settings':{'video.fast_forward':'toggle','video.fast_forward_speed':'8','video.fast_loading':'1'}})
 s.cancel=cancel;p=a.Pilot(s,cancel);p.wait_village(60);p.ensure_fast_forward();rp=r.RetroPilot(p,time.monotonic()+65);area=z.current(s);e=n.interactions(s,area)[5];x,y,zz=e['position']
 for i,(dx,dz) in enumerate([(-200,0),(0,-200),(0,200),(-300,0),(200,0),(-200,-200),(0,-350),(300,0)]):
  z.place(s,area['player'],(x+dx,y,zz+dz));p.pause(.8);ui=rp.ui()
  result={'offset':[dx,0,dz],'before':ui,'position':z.current(s)['position']};results.append(result)
  rp.button('x');p.pause(.6);result['state']=p.snapshot();p.confirm_shots([p.shoot(f'farm-{i}')])
  if result['state']['stage']!=139 or result['state']['scene']!=0:break
 c._write_json(s.dir/'standing.json',results);r.report(s.dir,{'state':'probe'},rp.transcript);print(s.dir)
finally:
 timer.cancel();w.stop();print('protected',before==[c._tree_digest(p) for p in paths]);print('pids',r.yakumo_pids())
