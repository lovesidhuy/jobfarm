# 6-hour trial run with auto-shutdown and full logging.
#
# Usage (in PowerShell on the VM):
#   cd C:\automation\automation_monorepo
#   .\trial_run.ps1
#
# What it does:
#   1. Kills leftover Chrome/chromedriver and clears profile locks
#   2. Schedules a Windows shutdown in 6 hours (21600s)
#   3. Starts the supervisor with stdout/stderr captured to a timestamped file
#   4. All per-bot logs continue writing to logs/<bot_name>/log.txt
#   5. CSVs go to all excels/, MongoDB collects DB events
#
# To cancel shutdown if needed:   shutdown /a
# To stop supervisor mid-run:     Ctrl+C in this window (shutdown still fires)

param(
    [int]$DurationHours = 6,
    [string]$Mode = "sequential"   # "sequential" | "parallel"
)

$ErrorActionPreference = "Continue"
$DurationSec = $DurationHours * 3600
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = "C:\automation\automation_monorepo\logs\trial_runs\$Timestamp"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$SupervisorLog = Join-Path $RunDir "supervisor.log"
$MetaFile = Join-Path $RunDir "run_meta.txt"

Write-Host "============================================="
Write-Host " 6-HOUR TRIAL RUN"
Write-Host "============================================="
Write-Host "Run dir:      $RunDir"
Write-Host "Duration:     $DurationHours hour(s)"
Write-Host "Mode:         $Mode"
Write-Host "Shutdown at:  $((Get-Date).AddHours($DurationHours))"
Write-Host ""

# Save metadata
@"
Trial run started: $(Get-Date)
Duration: $DurationHours hours
Mode: $Mode
Scheduled shutdown: $((Get-Date).AddSeconds($DurationSec))
Hostname: $env:COMPUTERNAME
Git HEAD: $(git -C C:\automation rev-parse --short HEAD 2>$null)
"@ | Set-Content $MetaFile

# 1. Cleanup
Write-Host "[1/4] Cleaning up stale Chrome processes..."
& "C:\automation\automation_monorepo\cleanup_chrome.ps1"

# 2. Pull latest code
Write-Host "[2/4] Pulling latest code..."
Push-Location C:\automation
git pull origin main 2>&1 | Tee-Object -FilePath (Join-Path $RunDir "git_pull.log")
Pop-Location

# 3. Schedule Windows shutdown for AWS EC2 instance.
#    Since we are now on AWS, an OS shutdown transitions the EC2 instance to
#    stopped state and STOPS compute billing.
Write-Host "[3/4] Scheduling Windows shutdown in $DurationHours hour(s) as a watchdog..."
shutdown /s /t $DurationSec

# 4. Start supervisor
Write-Host "[4/4] Starting supervisor..."
Write-Host "   Logs: $SupervisorLog"
Write-Host "   Per-bot logs: C:\automation\automation_monorepo\logs\<bot_name>\log.txt"
Write-Host ""
Write-Host "Press Ctrl+C to stop the supervisor (VM will still shutdown at scheduled time)"
Write-Host "To cancel shutdown: shutdown /a"
Write-Host ""

Push-Location C:\automation\automation_monorepo
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$args = @("--include-not-ok")
if ($Mode -eq "parallel") { $args += "--parallel" }
python supervisor.py @args 2>&1 | Tee-Object -FilePath $SupervisorLog
Pop-Location

Write-Host ""
Write-Host "Supervisor exited. Shutting down VM now to stop billing..."
Write-Host "Run dir: $RunDir"
shutdown /a 2>$null
shutdown /s /t 0
