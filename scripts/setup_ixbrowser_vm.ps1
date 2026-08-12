<#
.SYNOPSIS
    End-to-end ixBrowser setup on the Windows automation VM.

.DESCRIPTION
    Idempotent script that:
      1. Downloads + silently installs ixBrowser (skips if already present)
      2. Launches ixBrowser desktop (skips if already running)
      3. Waits for Local API on port 53200
      4. Creates / imports profiles for all supervised bots via API
      5. Attaches bots to profiles (writes IXBROWSER_PROFILE_ID_* to .env)
      6. Validates sessions by opening + closing each profile

    Designed to run unattended from Ansible, GitHub Actions, or interactively.

.PARAMETER InstallerUrl
    Direct URL to ixBrowser .exe installer. Defaults to latest known version.

.PARAMETER InstallDir
    ixBrowser installation directory. Default: C:\Program Files\ixBrowser

.PARAMETER MonorepoDir
    Path to automation_monorepo on the VM. Default: C:\automation\automation_monorepo

.PARAMETER ApiHost
    ixBrowser Local API host. Default: 127.0.0.1

.PARAMETER ApiPort
    ixBrowser Local API port. Default: 53200

.PARAMETER ApiTimeout
    Seconds to wait for Local API to become available after launch. Default: 120

.PARAMETER SkipInstall
    Skip download + install (assume ixBrowser is already installed).

.PARAMETER SkipLaunch
    Skip launching ixBrowser desktop (assume it is already running).

.PARAMETER WriteEnv
    Write IXBROWSER_PROFILE_ID_* values to the monorepo .env file.

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

.EXAMPLE
    # Full setup (interactive or from Ansible)
    .\scripts\setup_ixbrowser_vm.ps1 -WriteEnv

    # Skip install, just create profiles + validate
    .\scripts\setup_ixbrowser_vm.ps1 -SkipInstall -WriteEnv

    # Update proxy on existing profiles (requires force flag)
    .\scripts\setup_ixbrowser_vm.ps1 -SkipInstall -UpdateProxy -ForceProxyUpdate -ProxyUrl "socks5://user:pass@host:port"
#>
[CmdletBinding()]
param(
    [string]$InstallerUrl = "https://d.ixbrowser.com/ixbrowser/version/ixBrowser_Setup_2_8_15.exe",
    [string]$InstallDir   = "C:\Program Files\ixBrowser",
    [string]$MonorepoDir  = "C:\automation\automation_monorepo",
    [string]$ApiHost      = "127.0.0.1",
    [int]   $ApiPort      = 53200,
    [int]   $ApiTimeout   = 120,
    [switch]$SkipInstall,
    [switch]$SkipLaunch,
    [switch]$WriteEnv,
    [switch]$UpdateProxy,
    [switch]$ForceProxyUpdate,
    [string]$ProxyUrl = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # Speed up Invoke-WebRequest

# -- Helpers -------------------------------------------------------------------

function Write-Step  { param([string]$Msg) Write-Host "`n=== $Msg ===" -ForegroundColor Cyan }
function Write-Ok    { param([string]$Msg) Write-Host "  OK: $Msg"    -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "  WARN: $Msg"  -ForegroundColor Yellow }
function Write-Fail  { param([string]$Msg) Write-Host "  FAIL: $Msg"  -ForegroundColor Red }

function Test-ApiReachable {
    try {
        $r = Invoke-RestMethod -Uri "http://${ApiHost}:${ApiPort}/api/profile/list?limit=1" `
             -Method Get -TimeoutSec 5 -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Invoke-IxApi {
    param(
        [string]$Endpoint,
        [string]$Method = "GET",
        [hashtable]$Body = @{}
    )
    $uri = "http://${ApiHost}:${ApiPort}${Endpoint}"
    $params = @{ Uri = $uri; Method = $Method; ContentType = "application/json"; TimeoutSec = 30 }
    if ($Method -ne "GET" -and $Body.Count -gt 0) {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
    }
    $resp = Invoke-RestMethod @params
    return $resp
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

$ixExe = Join-Path $InstallDir "ixBrowser.exe"

if (-not $SkipInstall) {
    Write-Step "Step 1: Download + Install ixBrowser"

    if (Test-Path $ixExe) {
        Write-Ok "ixBrowser already installed at $ixExe"
    } else {
        $tempInstaller = Join-Path $env:TEMP "ixBrowser-Setup.exe"

        Write-Host "  Downloading from $InstallerUrl ..."
        try {
            if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                Write-Host "  Using curl.exe to download..."
                curl.exe -L -o $tempInstaller $InstallerUrl
            } else {
                Write-Host "  Using System.Net.WebClient to download..."
                $webclient = New-Object System.Net.WebClient
                $webclient.DownloadFile($InstallerUrl, $tempInstaller)
            }
            if (-not (Test-Path $tempInstaller) -or (Get-Item $tempInstaller).Length -lt 1MB) {
                throw "Downloaded file is missing or too small."
            }
            Write-Ok "Downloaded to $tempInstaller ($('{0:N0}' -f ((Get-Item $tempInstaller).Length / 1MB)) MB)"
        } catch {
            Write-Fail "Download failed: $_"
            Write-Host "  Set IXBROWSER_INSTALLER_URL in Infisical or download manually from https://www.ixbrowser.com/"
            exit 1
        }

        Write-Host "  Installing silently ..."
        $proc = Start-Process -FilePath $tempInstaller -ArgumentList "/S" -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            Write-Warn "Installer exited with code $($proc.ExitCode) (may still have succeeded)"
        }

        # Check common install locations
        $searchPaths = @(
            "C:\Program Files\ixBrowser\ixBrowser.exe",
            "C:\Program Files (x86)\ixBrowser\ixBrowser.exe",
            "$env:LOCALAPPDATA\ixBrowser\ixBrowser.exe"
        )
        $found = $searchPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($found) {
            $ixExe = $found
            Write-Ok "ixBrowser installed at $ixExe"
        } else {
            Write-Fail "ixBrowser.exe not found after install. Check installation manually."
            exit 1
        }

        # Cleanup
        Remove-Item $tempInstaller -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Step "Step 1: Install (SKIPPED)"
    if (-not (Test-Path $ixExe)) {
        # Try alternate locations
        $altPaths = @(
            "C:\Program Files (x86)\ixBrowser\ixBrowser.exe",
            "$env:LOCALAPPDATA\ixBrowser\ixBrowser.exe"
        )
        $found = $altPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($found) { $ixExe = $found }
        else {
            Write-Fail "ixBrowser not found. Remove -SkipInstall or install manually."
            exit 1
        }
    }
    Write-Ok "Using $ixExe"
}

# -- Step 2: Launch ixBrowser --------------------------------------------------

Write-Step "Step 2: Launch ixBrowser desktop"

if (-not $SkipLaunch) {
    # Check if already running
    $running = Get-Process -Name "ixBrowser" -ErrorAction SilentlyContinue
    if ($running) {
        Write-Ok "ixBrowser is already running (PID $($running.Id -join ', '))"
    } else {
        # Restore pre-authenticated session if zip is present
        $appData = [System.Environment]::GetFolderPath('ApplicationData')
        $ixFolder = Join-Path $appData "ixBrowser"
        $zipCandidates = @(
            (Join-Path $MonorepoDir "ixbrowser_session.zip")
            (Join-Path (Split-Path $MonorepoDir -Parent) "ixbrowser_session.zip")
            "C:\automation\ixbrowser_session.zip"
        )
        $zipPath = $zipCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

        if ($zipPath) {
            Write-Host "  Found pre-authenticated session zip: $zipPath"
            Write-Host "  Restoring to AppData..."
            if (Test-Path $ixFolder) {
                Remove-Item -Path $ixFolder -Recurse -Force -ErrorAction SilentlyContinue
            }
            try {
                Expand-Archive -Path $zipPath -DestinationPath $appData -Force
                Write-Ok "Session restored successfully"
            } catch {
                Write-Warn "Failed to extract session zip: $_"
            }
        } else {
            Write-Warn "No pre-authenticated ixbrowser_session.zip found. Manual login will be required."
        }

        Write-Host "  Starting ixBrowser via Scheduled Task..."
        $TaskName = "LaunchixBrowser"
        $Action = New-ScheduledTaskAction -Execute $ixExe
        $Principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive
        $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Principal $Principal -Settings $Settings -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 15
        $running = Get-Process -Name "ixBrowser" -ErrorAction SilentlyContinue
        if ($running) {
            Write-Ok "ixBrowser launched (PID $($running.Id -join ', '))"
        } else {
            Write-Warn "ixBrowser process not detected -- it may need manual sign-in first"
        }
    }
} else {
    Write-Step "Step 2: Launch (SKIPPED)"
}

# -- Step 3: Wait for Local API ------------------------------------------------

Write-Step "Step 3: Check Local API (http://${ApiHost}:${ApiPort})"

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
    - ixBrowser desktop is not running (launch it)
    - You have not signed in to ixBrowser (sign in once manually)
    - Local API is disabled (Settings > Local API > Enable)
    - Firewall is blocking port $ApiPort

  After fixing, re-run this script with -SkipInstall
"@
    exit 1
}

# -- Step 4: Create / import profiles ------------------------------------------

Write-Step "Step 4: Create / import profiles via Local API"

# Use the Python auto_ixbrowser_setup.py for profile creation -- it already
# handles proxy config, fingerprinting, and all 5 bot profiles.
$setupScript = Join-Path $MonorepoDir "scripts\auto_ixbrowser_setup.py"

if (-not (Test-Path $setupScript)) {
    Write-Fail "auto_ixbrowser_setup.py not found at $setupScript"
    Write-Host "  Ensure the repo is synced to $MonorepoDir"
    exit 1
}

# Refresh PATH so Python is available
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

$writeEnvFlag = if ($WriteEnv) { "--write-env" } else { "" }
$pythonArgs = @("$setupScript", "--auto", "--host", $ApiHost, "--port", $ApiPort)
if ($WriteEnv) { $pythonArgs += "--write-env" }
if ($UpdateProxy) {
    $pythonArgs += "--update-proxy"
    # Pass force flag -- the Python script checks both the CLI flag and
    # the FORCE_UPDATE_PROFILE_PROXY env var independently.
    $envForce = $env:FORCE_UPDATE_PROFILE_PROXY
    if ($ForceProxyUpdate -or ($envForce -and $envForce -match '^(true|1|yes)$')) {
        $pythonArgs += "--force-proxy-update"
    } else {
        Write-Warn "Proxy update requested but -ForceProxyUpdate not set."
        Write-Warn "Set -ForceProxyUpdate or FORCE_UPDATE_PROFILE_PROXY=true to confirm."
        Write-Warn "Changing proxy on logged-in profiles may trigger portal re-verification."
    }
}

# Resolve proxy: -ProxyUrl param > PROXY_URL env var
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
    Write-Warn "Profile setup exited with code $exitCode (some profiles may need manual creation)"
}

# -- Step 5: Attach bots to profiles -------------------------------------------

Write-Step "Step 5: Attach bots to profiles"

# Read the .env file to verify IXBROWSER_PROFILE_ID_* entries exist
$envFile = Join-Path $MonorepoDir ".env"
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    $botNames = @("INDEED_IT", "INDEED_GENERAL", "GLASSDOOR_IT", "LINKEDIN_IT", "LINKEDIN_GENERAL")
    $attached = 0
    foreach ($bot in $botNames) {
        $key = "IXBROWSER_PROFILE_ID_$bot"
        if ($envContent -match "$key=(\d+)") {
            $profileId = $Matches[1]
            Write-Ok "$bot -> profile_id=$profileId"
            $attached++
        } else {
            Write-Warn "$bot -> not configured in .env"
        }
    }
    Write-Host "  $attached / $($botNames.Count) bots attached to profiles"
} else {
    Write-Warn ".env file not found at $envFile -- bot-profile mapping not verified"
}

# -- Step 6: Validate sessions -------------------------------------------------

Write-Step "Step 6: Validate sessions (open + close each profile)"

# Re-read profile IDs from .env for validation
$profileIds = @{}
if (Test-Path $envFile) {
    foreach ($line in (Get-Content $envFile)) {
        if ($line -match "^IXBROWSER_PROFILE_ID_(\w+)=(\d+)") {
            $profileIds[$Matches[1]] = [int]$Matches[2]
        }
    }
}

$validated = 0
$failed    = 0
foreach ($entry in $profileIds.GetEnumerator()) {
    $botName   = $entry.Key
    $profileId = $entry.Value
    Write-Host "  Validating $botName (profile_id=$profileId) ..." -NoNewline

    try {
        # Open profile via API (v2 POST)
        $openResult = Invoke-IxApi -Endpoint "/api/v2/profile-open" -Method "POST" -Body @{ profile_id = $profileId }

        if ($openResult.error.code -eq 0 -or $openResult.data.debugging_address) {
            Write-Host " OPEN" -ForegroundColor Green -NoNewline

            # Brief pause to let the browser initialize
            Start-Sleep -Seconds 3

            # Close profile
            try {
                $closeResult = Invoke-IxApi -Endpoint "/api/v2/profile-close" -Method "POST" -Body @{ profile_id = $profileId }
                Write-Host " -> CLOSED" -ForegroundColor Green
                $validated++
            } catch {
                Write-Host " -> close failed (non-critical)" -ForegroundColor Yellow
                $validated++  # open succeeded, that's the important part
            }
        } else {
            $msg = $openResult.error.message
            Write-Host " FAILED: $msg" -ForegroundColor Red
            $failed++
        }
    } catch {
        Write-Host " ERROR: $_" -ForegroundColor Red
        $failed++
    }
}

# -- Summary -------------------------------------------------------------------

Write-Step "SUMMARY"
Write-Host ""
Write-Host "  ixBrowser exe    : $ixExe"
Write-Host "  Local API        : http://${ApiHost}:${ApiPort}"
Write-Host "  Profiles created : (see step 4 output)"
Write-Host "  Bots attached    : $attached / $($botNames.Count)"
Write-Host "  Sessions valid   : $validated / $($profileIds.Count)"
if ($failed -gt 0) {
    Write-Host "  Sessions failed  : $failed" -ForegroundColor Red
}
Write-Host ""

if ($failed -eq 0 -and $validated -gt 0) {
    Write-Host "  ALL SET -- ixBrowser is configured and sessions are valid." -ForegroundColor Green
    exit 0
} elseif ($validated -eq 0 -and $profileIds.Count -eq 0) {
    Write-Host "  No profiles found to validate. Run profile creation first." -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "  Some sessions failed validation. Check ixBrowser desktop." -ForegroundColor Yellow
    exit 1
}
