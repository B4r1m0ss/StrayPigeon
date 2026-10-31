# Windows launcher for the same-machine encrypted demo.
# Usage (PowerShell, from the project folder):
#   powershell -ExecutionPolicy Bypass -File scripts\demo_windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 scripts/demo_same_machine.py
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python scripts/demo_same_machine.py
} else {
    Write-Error "Python 3.10+ not found. Install it from python.org and enable 'Add python.exe to PATH'."
}
exit $LASTEXITCODE
