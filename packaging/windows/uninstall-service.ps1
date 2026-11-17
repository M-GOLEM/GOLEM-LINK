#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Stops and unregisters the GOLEM LINK service and removes its firewall rules.

.DESCRIPTION
    Configuration, database and logs in %ProgramData%\GolemLink are kept by
    default. Pass -RemoveData to delete them as well (irreversible).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\GOLEM LINK",
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"
$svcExe = Join-Path $InstallDir "golem-link-service.exe"
$dataDir = Join-Path $env:ProgramData "GolemLink"

if (Get-Service -Name "golem-link" -ErrorAction SilentlyContinue) {
    if (Test-Path $svcExe) {
        & $svcExe stop | Out-Null
        & $svcExe uninstall | Out-Null
    } else {
        Stop-Service -Name "golem-link" -Force -ErrorAction SilentlyContinue
        sc.exe delete golem-link | Out-Null
    }
}

foreach ($name in @("GOLEM LINK Dashboard", "GOLEM LINK Peer API", "GOLEM LINK Discovery", "GOLEM LINK mDNS")) {
    Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
}

if ($RemoveData) {
    if (Test-Path $dataDir) {
        Remove-Item -Recurse -Force $dataDir
        Write-Host "Removed $dataDir (identity, database, logs, pairings)."
    }
} else {
    Write-Host "Kept $dataDir. Re-run with -RemoveData to delete identity, database and logs."
}

Write-Host "GOLEM LINK service and firewall rules removed."
