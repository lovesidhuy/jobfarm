# Start supervisor.py detached (for GitHub Actions SSM / manual runs).
#
# Examples:
#   .\start_supervisor.ps1 -Parallel -Once -IncludeNotOk -Shutdown
#   .\start_supervisor.ps1 -Portal indeed -Parallel -Once -IncludeNotOk -Shutdown
#   .\start_supervisor.ps1 -Only "indeed_it,linkedin_it" -Parallel -Once -IncludeNotOk

param(
    [string]$Only = "",
    [string]$Portal = "",
    [string]$Profile = "",
    [switch]$Parallel,
    [switch]$Once,
    [switch]$IncludeNotOk,
    [switch]$Shutdown,
    [switch]$StopExisting,
    [switch]$VisibleBrowser
)

$ErrorActionPreference = "Stop"
$MonorepoDir = "C:\automation\automation_monorepo"
$LogDir = Join-Path $MonorepoDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$stdout = Join-Path $LogDir "supervisor_stdout.log"
$stderr = Join-Path $LogDir "supervisor_stderr.log"

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:ADSPOWER_HEADLESS = if ($VisibleBrowser) { "0" } else { "1" }

$browserVendor = "adspower"
$envFile = Join-Path $MonorepoDir ".env"
if (Test-Path $envFile) {
    $vendorLine = Get-Content $envFile | Where-Object { $_ -match "^\s*BROWSER_VENDOR\s*=" } | Select-Object -Last 1
    if ($vendorLine) {
        $browserVendor = ($vendorLine -replace "^\s*BROWSER_VENDOR\s*=\s*", "").Trim().Trim('"').Trim("'").ToLowerInvariant()
    }
}
Write-Output "Browser vendor: $browserVendor"

# -- Stop existing ixBrowser processes first to prevent config lock issues -----
$runningIx = if ($browserVendor -eq "ixbrowser") { Get-Process -Name "ixBrowser" -ErrorAction SilentlyContinue }
if ($runningIx) {
    Write-Output "Stopping existing ixBrowser processes to release config locks..."
    Stop-Process -Name "ixBrowser" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

# -- Fix double-nesting and macOS paths in ixBrowser session AppData ----------
$appData = [System.Environment]::GetFolderPath('ApplicationData')
$ixFolder = Join-Path $appData "ixBrowser"
$nestedFolder = Join-Path $ixFolder "ixBrowser"

if ($browserVendor -eq "ixbrowser" -and (Test-Path $ixFolder)) {
    if (-not (Test-Path $nestedFolder)) {
        New-Item -ItemType Directory -Force -Path $nestedFolder | Out-Null
    }
    # Sync configs and cache files to the nested folder
    Get-ChildItem -Path $ixFolder -Exclude "ixBrowser" | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $nestedFolder -Recurse -Force -ErrorAction SilentlyContinue
    }
    
    # Fix macOS paths in nested config file
    $iniPath = Join-Path $nestedFolder "ix_server.ini"
    if (Test-Path $iniPath) {
        $content = [System.IO.File]::ReadAllText($iniPath)
        if ($content -match "/Users/Jane") {
            Write-Output "Fixing macOS paths in ix_server.ini..."
            $content = $content -replace "/Users/Jane/Library/Application Support/ixBrowser/Browser Data", "C:/Users/Administrator/AppData/Roaming/ixBrowser/ixBrowser/Browser Data"
            $content = $content -replace "/Users/Jane/Library/Logs/ixBrowser", "C:/Users/Administrator/AppData/Roaming/ixBrowser/ixBrowser/logs"
            [System.IO.File]::WriteAllText($iniPath, $content)
        }
    }
    
    # Also fix macOS paths in outer config file just in case
    $outerIniPath = Join-Path $ixFolder "ix_server.ini"
    if (Test-Path $outerIniPath) {
        $content = [System.IO.File]::ReadAllText($outerIniPath)
        if ($content -match "/Users/Jane") {
            $content = $content -replace "/Users/Jane/Library/Application Support/ixBrowser/Browser Data", "C:/Users/Administrator/AppData/Roaming/ixBrowser/ixBrowser/Browser Data"
            $content = $content -replace "/Users/Jane/Library/Logs/ixBrowser", "C:/Users/Administrator/AppData/Roaming/ixBrowser/ixBrowser/logs"
            [System.IO.File]::WriteAllText($outerIniPath, $content)
        }
    }
}

if ($StopExisting) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*supervisor.py*" } |
        ForEach-Object {
            Write-Output "Stopping existing supervisor PID $($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 2
}

$argsList = @("supervisor.py")
if ($Only) { $argsList += @("--only", $Only) }
if ($Portal) { $argsList += @("--portal", $Portal) }
if ($Profile) { $argsList += @("--profile", $Profile) }
if ($Parallel) { $argsList += "--parallel" }
if ($Once) { $argsList += "--once" }
if ($IncludeNotOk) { $argsList += "--include-not-ok" }
if ($Shutdown) { $argsList += "--shutdown" }

if ($browserVendor -eq "adspower") {
    $adsPowerMode = if ($VisibleBrowser) { "visible" } else { "headless" }
    Write-Output "AdsPower mode - ensuring $adsPowerMode daemon on :50325..."
    $adsExe = $null
    foreach ($candidate in @(
        'C:\Program Files (x86)\AdsPower\AdsPower Global.exe',
        'C:\Program Files\AdsPower Global\AdsPower Global.exe'
    )) {
        if (Test-Path $candidate) { $adsExe = $candidate; break }
    }
    $apiKey = $null
    if (Test-Path $envFile) {
        $keyLine = Get-Content $envFile | Where-Object { $_ -match '^\s*ADSPOWER_API_KEY\s*=' } | Select-Object -Last 1
        if ($keyLine) {
            $apiKey = ($keyLine -replace '^\s*ADSPOWER_API_KEY\s*=\s*', '').Trim().Trim('"').Trim("'")
        }
    }
    if (-not $adsExe -or -not $apiKey) {
        throw "AdsPower not installed or ADSPOWER_API_KEY missing"
    }
    # SSM runs as SYSTEM, whose AdsPower data directory has no browser kernels.
    # Always restart the daemon from the Administrator interactive task below.
    Write-Output "Restarting AdsPower in the Administrator session..."
    Get-Process -Name "AdsPower Global", "SunBrowser" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

$ixExe = 'C:\Program Files\ixBrowser\ixBrowser.exe'
if ($browserVendor -eq "ixbrowser" -and (Test-Path $ixExe)) {
    $running = Get-Process -Name "ixBrowser" -ErrorAction SilentlyContinue
    if (-not $running) {
        Write-Output "ixBrowser is not running. Launching it via Scheduled Task..."
        $ixTaskName = "LaunchixBrowser"
        $ixAction = New-ScheduledTaskAction -Execute $ixExe
        $ixPrincipal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive
        $ixSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $ixTaskName -Action $ixAction -Principal $ixPrincipal -Settings $ixSettings -Force | Out-Null
        Start-ScheduledTask -TaskName $ixTaskName
        
        # Wait for the Local API to become reachable
        $timeout = 90
        $start = Get-Date
        while (((Get-Date) - $start).TotalSeconds -lt $timeout) {
            try {
                $r = Invoke-RestMethod -Uri 'http://127.0.0.1:53200/api/profile/list?limit=1' -TimeoutSec 5
                Write-Output "ixBrowser Local API is reachable"
                break
            } catch {
                Start-Sleep -Seconds 5
            }
        }
    }
}

Write-Output "Starting: python $($argsList -join ' ')"
Write-Output "Logs: $stdout"
Set-Content -Path $stdout -Value ""
Set-Content -Path $stderr -Value ""

$TaskName = "RunSupervisor"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$adsPowerBootstrap = ""
if ($browserVendor -eq "adspower") {
    # The daemon must run as Administrator so it sees that user's installed
    # kernels and profile cache instead of SYSTEM's empty AdsPower data folder.
    $adsPowerBootstrap = "Start-Process -FilePath '$adsExe' -ArgumentList '--headless=true','--api-key=$apiKey','--api-port=50325' -WindowStyle Hidden; `$deadline = (Get-Date).AddSeconds(120); while ((Get-Date) -lt `$deadline) { try { Invoke-RestMethod -Uri 'http://127.0.0.1:50325/status' -TimeoutSec 5 | Out-Null; break } catch { Start-Sleep -Seconds 5 } }; "
}
$psCommand = "& { cd '$MonorepoDir'; `$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path', 'User'); `$env:PYTHONIOENCODING = 'utf-8'; `$env:PYTHONUTF8 = '1'; `$env:PYTHONUNBUFFERED = '1'; `$env:ADSPOWER_HEADLESS = '$($env:ADSPOWER_HEADLESS)'; $adsPowerBootstrap python -u $($argsList -join ' ') > '$stdout' 2> '$stderr' }"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$psCommand`""
$Principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Output "Supervisor scheduled task '$TaskName' registered and started."
