"""Compare a failing public UI fixture with the original console parser."""
from pathlib import Path
import subprocess, os
root=Path(__file__).resolve().parents[1];repo=root/'psprecomp-mhfu';logs=root/'work/atelier-retro-baseline-20261027'
source=repo/'profiles/mhp3rd/host/debug/debug_console.cpp'
current=source.read_bytes();(logs/'debug_console.current.cpp').write_bytes(current)
baseline=subprocess.run(['git','-c','safe.directory='+str(repo),'show','HEAD:profiles/mhp3rd/host/debug/debug_console.cpp'],cwd=repo,capture_output=True,check=True).stdout
try:
 source.write_bytes(baseline)
 command=f'call "{root / "deps/env.cmd"}" && cmake --build "{repo / "out/mhfu-probe"}" -j2 --target mhp3rd_renderer_ui_tests && ctest --test-dir "{repo / "out/mhfu-probe"}" -R "^mhp3rd_renderer_ui_tests$" --output-on-failure --timeout 60'
 with (logs/'ctest-baseline-console.log').open('w',encoding='utf-8') as log:
  p=subprocess.run(f'cmd /d /s /c "{command}"',stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
 print('Baseline fixture exit:',p.returncode)
finally:
 source.write_bytes(current);os.utime(source,None)
 print('Current console source restored')
