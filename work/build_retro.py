from pathlib import Path
import subprocess
root = Path(__file__).resolve().parents[1]
import os,sys
for name in ('host/debug/mhfu_tools.cpp', 'host/debug/debug_console.cpp', 'tests/debug_tools_tests.cpp'):
    os.utime(root / 'psprecomp-mhfu/profiles/mhp3rd' / name, None)
targets = 'psprecomp_test_binaries' if '--all-tests' in sys.argv else 'Yakumo mhp3rd_debug_tools_tests'
command = f'call "{root / "deps/env.cmd"}" && cmake --build "{root / "psprecomp-mhfu/out/mhfu-probe"}" -j2 --target {targets}'
with (root / ('work/atelier-retro-baseline-20261027/build-tests.log' if '--all-tests' in sys.argv else 'work/atelier-retro-baseline-20261027/build.log')).open('w', encoding='utf-8') as log:
    result = subprocess.run(f'cmd /d /s /c "{command}"', stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
print(result.returncode)
raise SystemExit(result.returncode)
