@echo off
rem Atelier MHFU: prepare, replay, capture and compare runs of the PSPRecomp build.
setlocal
cd /d "%~dp0"
if not exist "atelier-psprecomp" mkdir "atelier-psprecomp"
start "" "deps\venv\Scripts\pythonw.exe" "outils\atelier-mhfu\atelier_ui.py" --project "%~dp0." 1>>"atelier-psprecomp\atelier-ui.log" 2>&1
