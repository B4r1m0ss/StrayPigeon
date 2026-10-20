# Build two standalone Windows console exes with PyInstaller.
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
# Output:
#   dist\StrayPigeon-setup.exe
#   dist\StrayPigeon-run.exe
#
# Notes:
# - Must be run on Windows; PyInstaller does not cross-compile.
# - The build machine needs Python 3.10+ and internet access for pip.
# - The target machine does not need Python.
# - pair_test.py / demo_same_machine.py spawn Python child processes; they are
#   intentionally NOT packaged here. Use the source checkout for those tests.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
    $pyArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = "python"
    $pyArgs = @()
} else {
    throw "Python 3.10+ not found. Install from python.org and enable 'Add python.exe to PATH'."
}

& $python @pyArgs -m pip install -U pyinstaller cryptography

$common = @(
    "--noconfirm", "--clean", "--onefile", "--console",
    "--paths", ".",
    "--collect-all", "cryptography",
    "--hidden-import", "p2p.signal.imap_store",
    "--hidden-import", "p2p.signal.smtp_mail",
    "--hidden-import", "p2p.signal.file"
)

& $python @pyArgs -m PyInstaller @common --name StrayPigeon-setup scripts\setup.py
& $python @pyArgs -m PyInstaller @common --name StrayPigeon-run scripts\run_vault.py

Write-Host ""
Write-Host "Built:"
Write-Host "  dist\StrayPigeon-setup.exe"
Write-Host "  dist\StrayPigeon-run.exe"
Write-Host "Run them from a folder where you want state/ and invite files, or use absolute --vault paths."
