# Read supervisor flags from environment variables (set by GitHub Actions SSM).
#
#   $env:SUP_ONLY = "indeed_it,linkedin_it"
#   $env:SUP_PORTAL = "indeed"
#   $env:SUP_PROFILE = "it"
#   $env:SUP_PARALLEL = "true"
#   $env:SUP_ONCE = "true"
#   $env:SUP_INCLUDE_NOT_OK = "true"
#   $env:SUP_SHUTDOWN = "true"
#   $env:SUP_VISIBLE_BROWSER = "true"
#   powershell -File start_supervisor_from_env.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$flags = @{ StopExisting = $true }
if ($env:SUP_ONLY) { $flags.Only = $env:SUP_ONLY }
if ($env:SUP_PORTAL) { $flags.Portal = $env:SUP_PORTAL }
if ($env:SUP_PROFILE) { $flags.Profile = $env:SUP_PROFILE }
if ($env:SUP_PARALLEL -eq "true") { $flags.Parallel = $true }
if ($env:SUP_ONCE -eq "true") { $flags.Once = $true }
if ($env:SUP_INCLUDE_NOT_OK -eq "true") { $flags.IncludeNotOk = $true }
if ($env:SUP_SHUTDOWN -eq "true") { $flags.Shutdown = $true }
if ($env:SUP_VISIBLE_BROWSER -eq "true") { $flags.VisibleBrowser = $true }

& (Join-Path $scriptDir "start_supervisor.ps1") @flags
if (-not $?) {
    throw "start_supervisor.ps1 failed"
}
