import json,sys,re
from pathlib import Path
o=Path(sys.argv[1])
for rec in json.loads((o/'advance-history.json').read_text(encoding='utf-8')):
    s=rec['stop'] or {}
    print('iter',rec['iteration'],'exit',rec['exit_code'],{k:s.get(k) for k in ('pc','ra','reason','actual_word','service_occurrence') if s.get(k) is not None})
for log in sorted(o.glob('advance-*.stdout.jsonl')):
    text=log.read_text(encoding='utf-8',errors='replace')
    fin=[l for l in text.splitlines()[-5:] if '"final_state"' in l]
    due=re.findall(r'"due":(\d+)',text[-20000:])
    print(log.name,'vblanks',text.count('"run_vblank_interrupt"'),'due_us',due[-1] if due else None,
          'ram',re.search(r'"memory_sha256":"(\w{12})',fin[0]).group(1) if fin else None,
          'interpreted_entries',text.count('"interpreted_entry"'))
for b in sorted(o.glob('advance-*-build.log')):
    m=re.search(r'"timing": (\{[^}]*\})',b.read_text(encoding='utf-8',errors='replace'))
    print(b.name,json.loads(m.group(1)) if m else 'no timing')
frames=sorted((o/'frames').glob('*.bmp'));print('frames',len(frames),frames[-1].name if frames else None)
