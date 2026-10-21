# Allow StrayPigeon's UDP port through Windows Defender Firewall.
#
# This script is launched elevated by the GUI (UAC). It only opens one
# inbound UDP port; it does NOT disable the firewall or open all ports.
#
# Manual use (admin PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\allow_firewall.ps1 -Port 40000
#   powershell -ExecutionPolicy Bypass -File scripts\allow_firewall.ps1 -Port 40000 -Profile Any
param(
    [int]$Port = 40000,
    [ValidateSet("Private,Domain", "Any")]
    [string]$Profile = "Private,Domain"
)

$ErrorActionPreference = "Stop"
$name = "StrayPigeon UDP $Port"

$existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
if ($existing) {
    Set-NetFirewallRule -DisplayName $name -Enabled True -Profile $Profile -Action Allow
    Write-Host "Updated firewall rule: $name (profile $Profile)"
} else {
    New-NetFirewallRule `
        -DisplayName $name `
        -Direction Inbound `
        -Protocol UDP `
        -LocalPort $Port `
        -Action Allow `
        -Profile $Profile | Out-Null
    Write-Host "Created firewall rule: $name (UDP $Port, profile $Profile)"
}

Write-Host "Only UDP $Port is opened; the firewall remains enabled."
