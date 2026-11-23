@echo off
rem Build the captured overlay libraries with the configured MSVC environment.
call "%~dp0..\..\deps\env.cmd"
if errorlevel 1 exit /b %errorlevel%
"%~dp0..\..\deps\venv\Scripts\python.exe" "%~dp0precompile_overlays.py"
exit /b %errorlevel%
