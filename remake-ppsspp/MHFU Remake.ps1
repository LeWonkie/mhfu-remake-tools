# MHFU Remake launcher: isolated PPSSPP v1.20.4 with its own memstick.
# First launch applies the preset; the game starts with its own fresh saves.
param([string]$Iso = 'G:\PSP\Monster Hunter Portable 2nd G (English v1.4.0)[FUComplete].iso')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $root 'ppsspp\PPSSPPWindows64.exe'
$memstick = Join-Path $root 'ppsspp\memstick\PSP'
if (-not (Test-Path -LiteralPath $exe)) { throw "PPSSPP absent : $exe" }
if (-not (Test-Path -LiteralPath $Iso)) { throw "ISO introuvable : $Iso" }
$ini = Join-Path $memstick 'SYSTEM\ppsspp.ini'
if (-not (Test-Path -LiteralPath $ini)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $ini) | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'preset\PSP\SYSTEM\ppsspp.ini') -Destination $ini
}
Start-Process -FilePath $exe -ArgumentList ('"' + $Iso + '"')
