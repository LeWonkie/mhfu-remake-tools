@echo off
call "%~dp0..\deps\env.cmd"
if errorlevel 1 exit /b %errorlevel%
cmake --build "%~dp0..\psprecomp-mhfu\out\mhfu-probe" --target Yakumo -j2
if errorlevel 1 exit /b %errorlevel%
ctest --test-dir "%~dp0..\psprecomp-mhfu\out\mhfu-probe" -R "^mhp3rd_debug_tools_tests$" --output-on-failure
