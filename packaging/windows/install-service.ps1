#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Registers GOLEM LINK as a Windows service using WinSW and opens the
    firewall ports for the Private network profile only.

.DESCRIPTION
    Run after GOLEM-LINK-Setup-Windows.exe installed the files, or after
    copying dist\GOLEM-LINK to $InstallDir manually. Idempotent: existing
    service registrations and firewall rules are replaced.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\GOLEM LINK",
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"

$svcExe = Join-Path $InstallDir "golem-link-service.exe"
$appExe = Join-Path $InstallDir "golem-link.exe"
$dataDir = Join-Path $env:ProgramData "GolemLink"
foreach ($path in @($svcExe, $appExe)) {
    if (-not (Test-Path $path)) { throw "missing $path - install or copy the GOLEM LINK files first" }
}

New-Item -ItemType Directory -Force -Path (Join-Path $dataDir "logs") | Out-Null

$existing = Get-Service -Name "golem-link" -ErrorAction SilentlyContinue
if ($existing) { & $svcExe stop | Out-Null; & $svcExe uninstall | Out-Null }
& $svcExe install | Out-Null
& $svcExe start | Out-Null
Start-Sleep -Seconds 3
Get-Service -Name "golem-link" | Select-Object Name, Status, StartType | Format-Table

if (-not $SkipFirewall) {
    $rules = @(
        @{ Name = "GOLEM LINK Dashboard"; Port = 8080; Protocol = "TCP" },
        @{ Name = "GOLEM LINK Peer API"; Port = 8443; Protocol = "TCP" },
        @{ Name = "GOLEM LINK Discovery"; Port = 45873; Protocol = "UDP" },
        @{ Name = "GOLEM LINK mDNS"; Port = 5353; Protocol = "UDP" }
    )
    foreach ($rule in $rules) {
        Remove-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow `
            -Protocol $rule.Protocol -LocalPort $rule.Port -Profile Private `
            -RemoteAddress LocalSubnet | Out-Null
    }
    Write-Host "Firewall rules added for the Private profile (local subnet only)."
}

Write-Host ""
Write-Host "GOLEM LINK service is registered."
Write-Host "Dashboard: http://localhost:8080  (LAN: http://<this-hostname>.local:8080)"
Write-Host "Logs:      $dataDir\logs"
