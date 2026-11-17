#Requires -Version 5.1
<#
.SYNOPSIS
    Builds dist\GOLEM-LINK (PyInstaller), the portable zip, and
    dist\GOLEM-LINK-Setup-Windows.exe (single-file installer). Inno Setup is
    optional with -Inno.

.DESCRIPTION
    Prerequisites: Node.js 18+ for the dashboard and network access once for
    PyInstaller and WinSW. Inno Setup 6 is only needed with -Inno.
    Run from any directory.

.PARAMETER WinSWChecksum
    Optional SHA-256 of WinSW-x64.exe. When supplied, the download is rejected
    if the hash does not match. Compute it once and pin it in CI.
#>
[CmdletBinding()]
param(
    [switch]$SkipDashboard,
    [switch]$Inno,
    [string]$WinSWChecksum = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$version = (Select-String -Path (Join-Path $Root "golem_link\version.py") -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Building GOLEM LINK $version"

# 1. Dashboard bundle -------------------------------------------------------
$webDist = Join-Path $Root "apps\dashboard\dist\index.html"
if (-not $SkipDashboard) {
    if (-not (Test-Path $webDist)) {
        Push-Location (Join-Path $Root "apps\dashboard")
        try { npm ci; npm run build } finally { Pop-Location }
    }
}
if (-not (Test-Path $webDist)) { throw "dashboard bundle missing: build apps/dashboard first" }

# 2. PyInstaller bundle -----------------------------------------------------
& $Python -m pip install --upgrade --disable-pip-version-check pyinstaller | Out-Host
& $Python -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $Root "dist") `
    --workpath (Join-Path $Root "build\pyinstaller") `
    (Join-Path $PSScriptRoot "golem-link.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
if (-not (Test-Path (Join-Path $Root "dist\GOLEM-LINK\golem-link.exe"))) { throw "dist\GOLEM-LINK\golem-link.exe was not produced" }

$portable = Join-Path $Root "dist\GOLEM-LINK-Portable-Windows.zip"
Compress-Archive -Path (Join-Path $Root "dist\GOLEM-LINK") -DestinationPath $portable -Force
Write-Host "Portable bundle: $portable"

# 3. WinSW service wrapper --------------------------------------------------
$winswDir = Join-Path $Root "build\windows"
$winsw = Join-Path $winswDir "WinSW-x64.exe"
New-Item -ItemType Directory -Force -Path $winswDir | Out-Null
$winswMinBytes = 5MB  # the real binary is ~18 MB; reject truncated downloads
if (-not (Test-Path $winsw) -or (Get-Item $winsw).Length -lt $winswMinBytes) {
    $url = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
    $partial = "$winsw.partial"
    Remove-Item $partial -Force -ErrorAction SilentlyContinue
    Write-Host "Downloading WinSW from $url"
    Invoke-WebRequest -Uri $url -OutFile $partial -UseBasicParsing
    if ((Get-Item $partial).Length -lt $winswMinBytes) {
        Remove-Item $partial -Force
        throw "WinSW download is incomplete; download $url manually into $winswDir"
    }
    Move-Item -Force $partial $winsw
}
$hash = (Get-FileHash -Algorithm SHA256 $winsw).Hash.ToLowerInvariant()
Write-Host "WinSW SHA-256: $hash"
if ($WinSWChecksum -and $hash -ne $WinSWChecksum.ToLowerInvariant()) {
    throw "WinSW checksum mismatch: expected $WinSWChecksum, got $hash"
}

# 4. Single-file installer --------------------------------------------------
if (-not $Inno) {
    Write-Host "Building the single-file installer (no Inno Setup needed)..."
    & $Python (Join-Path $PSScriptRoot "build_setup_exe.py")
    if ($LASTEXITCODE -ne 0) { throw "single-file installer build failed" }
    $setup = Join-Path $Root "dist\GOLEM-LINK-Setup-Windows.exe"
    if (-not (Test-Path $setup)) { throw "installer was not produced: $setup" }
    Write-Host ""
    Write-Host "Built $setup"
    Write-Host "Verify on a test machine before distributing; the installer registers a service and firewall rules."
    exit 0
}

# 4b. Optional Inno Setup ---------------------------------------------------
$isccCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) {
    throw "Inno Setup 6 (ISCC.exe) not found. Install it from https://jrsoftware.org/isdl.php and re-run."
}
& $iscc "/DMyAppVersion=$version" (Join-Path $PSScriptRoot "golem-link.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$setup = Join-Path $winswDir "GOLEM-LINK-Setup-Windows.exe"
if (-not (Test-Path $setup)) { throw "installer was not produced: $setup" }
Write-Host ""
Write-Host "Built $setup"
Write-Host "Verify on a test machine before distributing; the installer registers a service and firewall rules."
