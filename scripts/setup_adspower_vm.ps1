<#
.SYNOPSIS
    End-to-end AdsPower setup on the Windows automation VM.

.DESCRIPTION
    Idempotent script that:
      1. Downloads + silently installs AdsPower (skips if already present)
      2. Installs adspower-browser CLI via npm
      3. Launches AdsPower headless daemon with API Key (skips if already running)
      4. Waits for Local API on port 50325
      5. Creates / imports profiles for all supervised bots via API
      6. Attaches bots to profiles (writes ADSPOWER_PROFILE_ID_* to .env)

    Designed to run unattended from Ansible, GitHub Actions, or interactively.

.PARAMETER InstallerUrl
    Direct URL to AdsPower .exe installer. When empty, resolves the latest
    win64 build from https://www.adspower.com/download (CDN: version.adspower.net).

.PARAMETER InstallDir
    AdsPower installation directory. Default: C:\Program Files (x86)\AdsPower

.PARAMETER MonorepoDir
    Path to automation_monorepo on the VM. Default: C:\automation\automation_monorepo

.PARAMETER ApiHost
    AdsPower Local API host. Default: 127.0.0.1

.PARAMETER ApiPort
    AdsPower Local API port. Default: 50325

.PARAMETER ApiTimeout
    Seconds to wait for Local API to become available after launch. Default: 120

.PARAMETER SkipInstall
    Skip download + install (assume AdsPower is already installed).

.PARAMETER SkipLaunch
    Skip launching AdsPower daemon (assume it is already running).

.PARAMETER WriteEnv
    Write ADSPOWER_PROFILE_ID_* values to the monorepo .env file.

.PARAMETER UpdateProxy
    Update proxy on all existing profiles. Requires PROXY_URL env var or -ProxyUrl.
    Also requires -ForceProxyUpdate (or FORCE_UPDATE_PROFILE_PROXY=true env var)
    as a safety guard.

.PARAMETER ForceProxyUpdate
    Confirm proxy update. Required when -UpdateProxy is set.
    Can also be set via FORCE_UPDATE_PROFILE_PROXY=true env var.

.PARAMETER ProxyUrl
    Proxy URL to use for profiles (e.g. socks5://user:pass@host:port).
    Overrides PROXY_URL env var.

.PARAMETER ApiKey
    AdsPower API Key. Overrides ADSPOWER_API_KEY env var.
#>
[CmdletBinding()]
param(
    [string]$InstallerUrl = "",
    [string]$InstallDir   = "C:\Program Files (x86)\AdsPower",
    [string]$MonorepoDir  = "C:\automation\automation_monorepo",
    [string]$ApiHost      = "127.0.0.1",
    [int]   $ApiPort      = 50325,
    [int]   $ApiTimeout   = 120,
    [switch]$SkipInstall,
    [switch]$SkipLaunch,
    [switch]$WriteEnv,
    [switch]$UpdateProxy,
    [switch]$ForceProxyUpdate,
    [string]$ProxyUrl = "",
    [string]$ApiKey = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # Speed up Invoke-WebRequest

# -- Helpers -------------------------------------------------------------------

function Write-Step  { param([string]$Msg) Write-Host "`n=== $Msg ===" -ForegroundColor Cyan }
function Write-Ok    { param([string]$Msg) Write-Host "  OK: $Msg"    -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "  WARN: $Msg"  -ForegroundColor Yellow }
function Write-Fail  { param([string]$Msg) Write-Host "  FAIL: $Msg"  -ForegroundColor Red }

# Official CDN (version.adspower.net); share.adspower.net/download/win_x64 returns 404.
$AdsPowerInstallerFallback = "https://version.adspower.net/software/win64-global/8.6.3/AdsPower-Global-8.6.3-x64.exe"

function Resolve-AdsPowerInstallerUrl {
    param([string]$PinnedFallback = $AdsPowerInstallerFallback)
    try {
        $html = (Invoke-WebRequest -Uri "https://www.adspower.com/download" -UseBasicParsing -TimeoutSec 45).Content
        $candidates = [regex]::Matches(
            $html,
            'https://version\.adspower\.net/software/win64-global/([0-9.]+)/AdsPower-Global-[0-9.]+-x64\.exe'
        ) | ForEach-Object {
            [PSCustomObject]@{
                Url     = $_.Value
                Version = [version]$_.Groups[1].Value
            }
        }
        $best = $candidates | Sort-Object Version -Descending | Select-Object -First 1
        if ($best) { return $best.Url }
    } catch {
        Write-Warn "Could not resolve latest installer from adspower.com/download: $_"
    }
    return $PinnedFallback
}

function Test-ApiReachable {
    try {
        $r = Invoke-RestMethod -Uri "http://${ApiHost}:${ApiPort}/status" -Method Get -TimeoutSec 5 -ErrorAction Stop
        return $true
    } catch {
        # Try fall back to listing profiles if status requires auth or differs
        try {
            $headers = @{}
            if ($resolvedKey) { $headers["Authorization"] = "Bearer $resolvedKey" }
            $r = Invoke-RestMethod -Uri "http://${ApiHost}:${ApiPort}/api/v1/user/list?page_size=1" `
                 -Method Get -Headers $headers -TimeoutSec 5 -ErrorAction Stop
            return $true
        } catch {
            return $false
        }
    }
}

# Resolve API Key
$resolvedKey = if ($ApiKey) { $ApiKey } else { $env:ADSPOWER_API_KEY }
if (-not $resolvedKey) {
    # Try reading from .env file
    $envFile = Join-Path $MonorepoDir ".env"
    if (Test-Path $envFile) {
        $envContent = Get-Content $envFile
        foreach ($line in $envContent) {
            if ($line -match "^\s*ADSPOWER_API_KEY\s*=\s*(.+)\s*$") {
                $resolvedKey = $Matches[1].Trim().Trim('"').Trim("'")
                break
            }
        }
    }
}

# -- Step 0: Enable multiple concurrent RDP sessions --------------------------
# Allows both admin and dev to RDP in simultaneously for pair debugging.
$rdpRegPath = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server"
$currentVal = (Get-ItemProperty -Path $rdpRegPath -Name fSingleSessionPerUser -ErrorAction SilentlyContinue).fSingleSessionPerUser
if ($currentVal -ne 0) {
    Set-ItemProperty -Path $rdpRegPath -Name fSingleSessionPerUser -Value 0 -Type DWord
    Write-Ok "Enabled multiple concurrent RDP sessions (fSingleSessionPerUser=0)"
} else {
    Write-Ok "Multiple RDP sessions already enabled"
}

# -- Step 1: Download + Install ------------------------------------------------

$adsExe = Join-Path $InstallDir "AdsPower Global.exe"

if (-not $SkipInstall) {
    Write-Step "Step 1: Download + Install AdsPower"

    if (Test-Path $adsExe) {
        Write-Ok "AdsPower already installed at $adsExe"
    } else {
        $tempInstaller = Join-Path $env:TEMP "AdsPower-Setup.exe"

        if (-not $InstallerUrl) {
            $InstallerUrl = Resolve-AdsPowerInstallerUrl
        }
        $fallbackUrl = $AdsPowerInstallerFallback

        Write-Host "  Downloading from $InstallerUrl ..."
        try {
            Invoke-WebRequest -Uri $InstallerUrl -OutFile $tempInstaller -UseBasicParsing
            Write-Ok "Downloaded to $tempInstaller ($('{0:N0}' -f ((Get-Item $tempInstaller).Length / 1MB)) MB)"
        } catch {
            Write-Warn "Download from primary URL failed: $_. Trying fallback..."
            try {
                Invoke-WebRequest -Uri $fallbackUrl -OutFile $tempInstaller -UseBasicParsing
                Write-Ok "Downloaded from fallback URL to $tempInstaller ($('{0:N0}' -f ((Get-Item $tempInstaller).Length / 1MB)) MB)"
            } catch {
                Write-Fail "Download from both primary and fallback URLs failed: $_"
                exit 1
            }
        }

        Write-Host "  Installing silently ..."
        $proc = Start-Process -FilePath $tempInstaller -ArgumentList "/S" -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            Write-Warn "Installer exited with code $($proc.ExitCode) (may still have succeeded)"
        }

        # Check common install locations
        $searchPaths = @(
            "C:\Program Files (x86)\AdsPower\AdsPower Global.exe",
            "C:\Program Files\AdsPower Global\AdsPower Global.exe",
            "$env:LOCALAPPDATA\AdsPower\AdsPower Global.exe"
        )
        $found = $searchPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($found) {
            $adsExe = $found
            $InstallDir = Split-Path $found -Parent
            Write-Ok "AdsPower installed at $adsExe"
        } else {
            Write-Fail "AdsPower Global.exe not found after install. Check installation manually."
            exit 1
        }

        # Cleanup
        Remove-Item $tempInstaller -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Step "Step 1: Install (SKIPPED)"
    if (-not (Test-Path $adsExe)) {
        # Try alternate locations
        $altPaths = @(
            "C:\Program Files\AdsPower Global\AdsPower Global.exe",
            "$env:LOCALAPPDATA\AdsPower\AdsPower Global.exe"
        )
        $found = $altPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($found) { 
            $adsExe = $found 
            $InstallDir = Split-Path $found -Parent
        } else {
            Write-Fail "AdsPower not found. Remove -SkipInstall or install manually."
            exit 1
        }
    }
    Write-Ok "Using $adsExe"
}

# -- Step 2: Install CLI Package via npm ---------------------------------------

Write-Step "Step 2: Install adspower-browser CLI package"
try {
    # Refresh PATH so Node/npm are picked up
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    
    if (Get-Command "npm" -ErrorAction SilentlyContinue) {
        Write-Host "  Installing adspower-browser globally..."
        npm install -g adspower-browser
        Write-Ok "adspower-browser CLI installed"
    } else {
        Write-Warn "npm not found in PATH -- skipping global CLI package installation (daemon can still launch directly)"
    }
} catch {
    Write-Warn "Failed to install adspower-browser globally via npm: $_"
}

# -- Step 3: Launch AdsPower Headless Daemon -----------------------------------

Write-Step "Step 3: Launch AdsPower daemon (headless)"

if (-not $SkipLaunch) {
    if (-not $resolvedKey) {
        Write-Fail "Cannot start headless daemon without an API Key. Set ADSPOWER_API_KEY env var or pass -ApiKey."
        exit 1
    }

    # Check if already running
    $running = Get-Process -Name "AdsPower Global" -ErrorAction SilentlyContinue
    if ($running) {
        Write-Ok "AdsPower Global is already running (PID $($running.Id -join ', '))"
    } else {
        Write-Host "  Starting AdsPower in headless mode..."
        # Launching the executable directly is extremely robust as it bypasses shell wrappers
        Start-Process -FilePath $adsExe -ArgumentList "--headless=true", "--api-key=$resolvedKey", "--api-port=$ApiPort" -WindowStyle Hidden
        Start-Sleep -Seconds 15
        $running = Get-Process -Name "AdsPower Global" -ErrorAction SilentlyContinue
        if ($running) {
            Write-Ok "AdsPower Global daemon launched (PID $($running.Id -join ', '))"
        } else {
            Write-Warn "AdsPower process not detected in process list. Checking if API becomes reachable anyway..."
        }
    }
} else {
    Write-Step "Step 3: Launch (SKIPPED)"
}

# -- Step 4: Wait for Local API ------------------------------------------------

Write-Step "Step 4: Check Local API (http://${ApiHost}:${ApiPort})"

$deadline = (Get-Date).AddSeconds($ApiTimeout)
$apiReady = $false
while ((Get-Date) -lt $deadline) {
    if (Test-ApiReachable) {
        $apiReady = $true
        break
    }
    Write-Host "  Waiting for API ..." -NoNewline
    Start-Sleep -Seconds 5
    Write-Host ""
}

if ($apiReady) {
    Write-Ok "Local API is reachable"
} else {
    Write-Fail "Local API not reachable after ${ApiTimeout}s"
    Write-Host @"

  Possible causes:
    - AdsPower Global daemon is not running (check process list)
    - The API key provided is invalid or expired
    - Firewall is blocking port $ApiPort

  After fixing, re-run this script with -SkipInstall
"@
    exit 1
}

# -- Step 5: Create / import profiles ------------------------------------------

Write-Step "Step 5: Create / import profiles via Local API"

$setupScript = Join-Path $MonorepoDir "scripts\auto_adspower_setup.py"

if (-not (Test-Path $setupScript)) {
    Write-Fail "auto_adspower_setup.py not found at $setupScript"
    Write-Host "  Ensure the repo is synced to $MonorepoDir"
    exit 1
}

# Refresh PATH so Python is available
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

$pythonArgs = @("$setupScript", "--auto", "--host", $ApiHost, "--port", $ApiPort)
if ($WriteEnv) { $pythonArgs += "--write-env" }
if ($resolvedKey) { $pythonArgs += @("--api-key", $resolvedKey) }
if ($UpdateProxy) {
    $pythonArgs += "--update-proxy"
    $envForce = $env:FORCE_UPDATE_PROFILE_PROXY
    if ($ForceProxyUpdate -or ($envForce -and $envForce -match '^(true|1|yes)$')) {
        $pythonArgs += "--force-proxy-update"
    } else {
        Write-Warn "Proxy update requested but -ForceProxyUpdate not set."
    }
}

# Resolve proxy
$resolvedProxy = if ($ProxyUrl) { $ProxyUrl } else { $env:PROXY_URL }
if ($resolvedProxy) { $pythonArgs += @("--proxy", $resolvedProxy) }

Write-Host "  Running: python $($pythonArgs -join ' ')"
$result = & python @pythonArgs 2>&1
$exitCode = $LASTEXITCODE

# Display output
$result | ForEach-Object { Write-Host "  $_" }

if ($exitCode -eq 0) {
    Write-Ok "Profile setup completed"
} else {
    Write-Warn "Profile setup exited with code $exitCode"
}

# -- Step 6: Attach bots to profiles -------------------------------------------

Write-Step "Step 6: Verify bot-profile mappings in .env"

$envFile = Join-Path $MonorepoDir ".env"
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    $botNames = @("INDEED_IT", "INDEED_GENERAL", "GLASSDOOR_IT", "LINKEDIN_IT", "LINKEDIN_GENERAL")
    $attached = 0
    foreach ($bot in $botNames) {
        $key = "ADSPOWER_PROFILE_ID_$bot"
        if ($envContent -match "$key=(\w+)") {
            $profileId = $Matches[1]
            Write-Ok "$bot -> AdsPower user_id=$profileId"
            $attached++
        } else {
            Write-Warn "$bot -> not configured in .env"
        }
    }
    Write-Host "  $attached / $($botNames.Count) bots attached to AdsPower profiles"
} else {
    Write-Warn ".env file not found at $envFile"
}

# -- Summary -------------------------------------------------------------------

Write-Step "SUMMARY"
Write-Host ""
Write-Host "  AdsPower exe     : $adsExe"
Write-Host "  Local API        : http://${ApiHost}:${ApiPort}"
Write-Host "  Profiles setup   : (see step 5 output)"
Write-Host "  Bots attached    : $attached / $($botNames.Count)"
Write-Host ""

if ($attached -gt 0) {
    Write-Host "  ALL SET -- AdsPower is configured and daemon is running." -ForegroundColor Green
    exit 0
} else {
    Write-Host "  No profiles found or mapped. Check the setup script output above." -ForegroundColor Yellow
    exit 1
}
