# Pull session artifacts from S3 before a fresh deploy (ixBrowser cookies + session_registry).
# Used by Ansible on deploy; can also run manually on the VM.
#
#   powershell -ExecutionPolicy Bypass -File C:\automation\automation_monorepo\scripts\restore_session_from_s3.ps1

$ErrorActionPreference = "Continue"
$bucket = "jobbots-tfstate-bucket"
$installDir = "C:\automation"
$monorepoDir = "C:\automation\automation_monorepo"
$registryDir = Join-Path $monorepoDir "data\supervisor"

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

Write-Output "Restoring session artifacts from s3://$bucket/backups/ ..."

# ixBrowser session zip -> C:\automation\ixbrowser_session.zip (Ansible expands to AppData\Roaming)
$localZip = Join-Path $installDir "ixbrowser_session.zip"
if (-not (Test-Path $localZip)) {
    Write-Output "Downloading ixbrowser_session.zip ..."
    aws s3 cp "s3://$bucket/backups/ixbrowser_session.zip" $localZip 2>&1
    if ($LASTEXITCODE -eq 0 -and (Test-Path $localZip)) {
        Write-Output "ixbrowser_session.zip restored to $localZip"
    } else {
        Write-Warning "ixbrowser_session.zip not in S3 or download failed - manual session required."
    }
} else {
    Write-Output "ixbrowser_session.zip already present at $localZip - skipping S3 download."
}

# session_registry.json
New-Item -ItemType Directory -Force -Path $registryDir | Out-Null
$localRegistry = Join-Path $registryDir "session_registry.json"
if (-not (Test-Path $localRegistry)) {
    Write-Output "Downloading session_registry.json ..."
    aws s3 cp "s3://$bucket/backups/session_registry.json" $localRegistry 2>&1
    if ($LASTEXITCODE -eq 0 -and (Test-Path $localRegistry)) {
        Write-Output "session_registry.json restored."
    } else {
        Write-Warning "session_registry.json not in S3 - use --include-not-ok when starting supervisor."
    }
} else {
    Write-Output "session_registry.json already present - skipping S3 download."
}

# Chrome session zip -> C:\automation\chrome_profiles.zip
$localChromeZip = Join-Path $installDir "chrome_profiles.zip"
if (-not (Test-Path $localChromeZip)) {
    Write-Output "Downloading chrome_profiles.zip ..."
    aws s3 cp "s3://$bucket/backups/chrome_profiles.zip" $localChromeZip 2>&1
    if ($LASTEXITCODE -eq 0 -and (Test-Path $localChromeZip)) {
        Write-Output "chrome_profiles.zip restored to $localChromeZip"
    } else {
        Write-Warning "chrome_profiles.zip not in S3 or download failed."
    }
} else {
    Write-Output "chrome_profiles.zip already present at $localChromeZip - skipping S3 download."
}

# If chrome_profiles.zip is present, expand it to data\browser_profiles
if (Test-Path $localChromeZip) {
    Write-Output "Extracting chrome_profiles.zip to local Chrome profiles dir..."
    $chromeDest = Join-Path $monorepoDir "data\browser_profiles"
    if (Test-Path $chromeDest) {
        Remove-Item -Path $chromeDest -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $chromeDest -Force | Out-Null
    Expand-Archive -Path $localChromeZip -DestinationPath $chromeDest -Force
    Write-Output "chrome_profiles.zip extracted to $chromeDest"
}

Write-Output "Session restore from S3 complete."
