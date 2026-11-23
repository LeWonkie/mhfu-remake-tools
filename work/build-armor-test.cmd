@echo off
call "%~dp0..\deps\env.cmd"
if errorlevel 1 exit /b %errorlevel%
cmake --build "%~dp0..\psprecomp-mhfu\out\mhfu-probe" --target mhp3rd_debug_tools_tests -j2
if errorlevel 1 exit /b %errorlevel%
"%~dp0..\psprecomp-mhfu\out\mhfu-probe\bin\mhp3rd_debug_tools_tests.exe"
