from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parents[1]
selection='' if '--all' in sys.argv else '-R "^psprecomp_runtime_contract_tests$"'
command=f'call "{root / "deps/env.cmd"}" && ctest --test-dir "{root / "psprecomp-mhfu/out/mhfu-probe"}" {selection} --output-on-failure --timeout 90 -j2'
with (root/('work/atelier-retro-baseline-20261027/ctest-final.log' if '--all' in sys.argv else 'work/atelier-retro-baseline-20261027/ctest-runtime-env.log')).open('w',encoding='utf-8') as log:
 result=subprocess.run(f'cmd /d /s /c "{command}"',stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
print(result.returncode)
