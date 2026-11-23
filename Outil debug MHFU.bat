@echo off
rem Outil debug MHFU: plays game-mhfu with the developer commands (items, quest items, gathering, teleports, cheats).
setlocal
cd /d "%~dp0"
if not exist "sessions-psprecomp" mkdir "sessions-psprecomp"
start "" "deps\venv\Scripts\pythonw.exe" "outils\atelier-mhfu\outil_debug.py" --project "%~dp0." 1>>"sessions-psprecomp\outil-debug.log" 2>&1
