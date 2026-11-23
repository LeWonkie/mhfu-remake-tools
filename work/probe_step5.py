"""Bounded private gameplay probe; writes only placement and authorized test aids."""
import argparse
import json
from pathlib import Path
import struct
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_campaign as campaign
import atelier_core as core
import atelier_pilot as pilot_mod
import atelier_zones as zones
import deliver


def tables(session, area):
    definitions = area.get('definitions')
    if not definitions:
        return []
    raw = zones.read(session, definitions, 0x44)
    table = struct.unpack_from('<I', raw, 0x10)[0]
    count = struct.unpack_from('<b', raw, 0x3E)[0]
    if not 0 < count <= 64 or not 0x08800000 <= table < 0x09FFF000:
        return []
    entries = []
    block = zones.read(session, table, count*24)
    for i in range(count):
        identifier, kind, x,y,z,radius,angle = struct.unpack_from('<HH4fH', block, i*24)
        entries.append(dict(address=table+i*24,id=identifier,type=kind,position=[x,y,z],radius=radius,angle=angle))
    return entries


def resources(session, area):
    quest=zones.u32(session,zones.QUEST_PTR)
    manager=zones.u32(session,zones.STAGE_MANAGER_PTR)
    map_id=zones.u32(session,manager+zones.MAP_ID)
    count,ptr=struct.unpack('<II',zones.read(session,0x09CDF678+8*map_id,8))
    if not 0<count<=32:return []
    stages=struct.unpack(f'<{count}H',zones.read(session,ptr,count*2))
    if area['stage'] not in stages:return []
    base,offsets=struct.unpack('<I12xI',zones.read(session,quest+0x50,20))
    offset=zones.u32(session,offsets+4*stages.index(area['stage']))
    if offset==0:return []
    nodes=[]
    for index in range(40):
        raw=zones.read(session,base+offset+index*24,24)
        x,y,z,r,loot,remaining,kind,tier=struct.unpack('<4f4H',raw)
        if x==-1:break
        nodes.append(dict(address=base+offset+index*24,position=[x,y,z],radius=r,
                          loot=loot,remaining=remaining,type=kind,tier=tier))
    return nodes


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('quest',type=int)
    parser.add_argument('--hop',type=int)
    parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--speed',type=int,default=8)
    args=parser.parse_args()
    assert 60<=args.seconds<=600 and 1<=args.speed<=8
    w=core.Workshop(ROOT)
    cancel=threading.Event()
    timer=threading.Timer(args.seconds,cancel.set);timer.daemon=True;timer.start()
    folder=ROOT/'work/step5-probe';folder.mkdir(exist_ok=True)
    control=folder/'control.json'
    last=json.loads(control.read_text())['id'] if control.exists() else None
    s=None;history=[]
    try:
        scenario={'name':'campagne','label':'Step 5 gameplay probe','debug':True,'fast_boot':True,
                  'dump_overlays':True,'script':campaign.BOOT,'screenshot_every':0,
                  'settings':{'video.fast_forward':'toggle','video.fast_forward_speed':str(args.speed),
                              'video.fast_loading':'1'}}
        s=campaign.Session(w,'20261020-153034-ceda7b',args.seconds+15,
             variant=core.register_rtc_variant('normal',1609459200123456),scenario=scenario)
        s.cancel=cancel
        p=pilot_mod.Pilot(s,cancel=cancel)
        s.arm_loadout('best');p.wait_village(60)
        if args.speed>1:p.ensure_fast_forward()
        p.confirm_shots([p.shoot('step5-village',60)])
        p.depart(args.quest,60);p.wait_quest(args.quest,60)
        s.ask('hold health on','health',5)
        if args.hop:
            here=zones.current(s)
            ex=next(e for e in here['exits'] if e['destination']==args.hop)
            print('HOP',zones.traverse(s,here,ex,settle=1),flush=True)
        print('READY',s.run_id,flush=True)
        while not cancel.is_set() and s.alive():
            area=zones.current(s,timeout=3)
            gs=zones.u32(s,zones.GAMESYS_PTR)
            pouch=zones.read(s,gs+deliver.QUEST_POUCH,96)
            nodes=tables(s,area)
            harvest=resources(s,area)
            state={'run_id':s.run_id,'area':area,'nodes':nodes,'mission':zones.mission_state(s),
                   'pouch':[list(struct.unpack_from('<Hh',pouch,i*4)) for i in range(24)],
                   'monsters':zones.monsters(s),'objective':zones.mission_objectives(s,args.quest),
                   'interaction':zones.u32(s,area['player']+0x554),'resources':harvest}
            core._write_json(folder/'state.json',state)
            if control.exists():
                cmd=json.loads(control.read_text())
                if cmd['id']!=last:
                    last=cmd['id'];history.append({'command':cmd,'before':state})
                    if cmd.get('stop'):break
                    if 'hop' in cmd:
                        ex=next(e for e in area['exits'] if e['destination']==cmd['hop'])
                        history[-1]['hop']=zones.traverse(s,area,ex,settle=1)
                    if 'node' in cmd:
                        node=nodes[cmd['node']];x,y,z=node['position']
                        zones.place(s,area['player'],(x,y,z))
                    if 'resource' in cmd:
                        zones.place(s,area['player'],harvest[cmd['resource']]['position'])
                    if 'position' in cmd:zones.place(s,area['player'],cmd['position'])
                    if 'inputs' in cmd:
                        core.validate_script(';'.join(cmd['inputs']));s.inputs(cmd['inputs'])
                    if 'debug' in cmd:
                        for line in cmd['debug']:
                            if not line.startswith(('hold ','monsterhp ','pouch ')):
                                raise ValueError('unsupported debug aid')
                        w.debug_commands(cmd['debug'])
                    if cmd.get('return'):
                        def back():
                            snap=p.snapshot()
                            if snap['task']==10:return snap
                            p.ask(['quest results'],['results countdown'],1)
                            s.inputs(['0:pad b 6'])
                        p.until('village',back,90,poll=.3);p.wait_village(30)
                        history[-1]['village_ready']=True
                    if 'shot' in cmd:
                        history[-1]['shot']=p.confirm_shots([p.shoot(cmd['shot'],30)])
                    core._write_json(s.dir/'step5-probe.json',history)
                    print('STEP',last,flush=True)
                    if cmd.get('return'):break
            cancel.wait(.4)
    finally:
        timer.cancel()
        if s:core._write_json(s.dir/'step5-probe.json',history)
        w.stop()


if __name__=='__main__':main()
