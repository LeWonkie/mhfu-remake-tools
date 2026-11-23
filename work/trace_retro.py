"""Accumulate stdlib line coverage from real trials and the public Python suite."""
import sys,trace,runpy,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];logs=root/'work/atelier-retro-baseline-20261027'
counts=logs/'python-trace.counts';tracer=trace.Trace(count=True,trace=False,
    infile=str(counts) if counts.exists() else None,outfile=str(counts),ignoredirs=[sys.base_prefix,sys.prefix])
sys.path.insert(0,str(root/'outils/atelier-mhfu'))
args=sys.argv[1:];code=0
try:
 if args==['--tests']:
  sys.argv=['unittest','discover','-s',str(root/'outils/atelier-mhfu/tests'),'-q']
  tracer.runfunc(runpy.run_module,'unittest',run_name='__main__')
 else:
  sys.argv=[str(root/'outils/atelier-mhfu/atelier_retro.py'),*args]
  tracer.runfunc(runpy.run_path,sys.argv[0],run_name='__main__')
except SystemExit as e:code=e.code or 0
finally:
 result=tracer.results();result.write_results(show_missing=True,summary=False,coverdir=str(logs/'python-line-coverage'))
 summary={}
 for name in ('atelier_retro.py','retro_memory.py'):
  p=root/'outils/atelier-mhfu'/name;lines=set(trace._find_executable_linenos(str(p)))
  hit={line for (file,line),n in result.counts.items() if Path(file).resolve()==p.resolve() and n>0}
  summary[name]={'covered_lines':len(hit & lines),'executable_lines':len(lines),'line_percent':round(100*len(hit & lines)/len(lines),2)}
 (logs/'python-line-coverage.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
 print(json.dumps(summary))
raise SystemExit(code)
