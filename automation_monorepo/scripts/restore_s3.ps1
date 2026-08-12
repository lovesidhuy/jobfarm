$bucket = "jobbots-tfstate-bucket"
$installDir = "C:\automation"
$monorepoDir = "C:\automation\automation_monorepo"
$backupTemp = "C:\Windows\Temp\restore"
$zipPath = "C:\automation\latest_backup.zip"

# Refresh Path env
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Output "Starting Restore from S3..."

# 1. Download backup zip from S3
if (Test-Path $zipPath) {
    Remove-Item -Path $zipPath -Force
}

aws s3 cp "s3://$bucket/backups/latest_backup.zip" "$zipPath"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "No backup file found in S3 bucket backups/latest_backup.zip or aws s3 cp failed. Skipping restore."
    exit 0
}

# 2. Extract backup zip
if (Test-Path $backupTemp) {
    Remove-Item -Path $backupTemp -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $backupTemp -Force | Out-Null

Write-Output "Extracting backup..."
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $backupTemp)

# 3. Restore ixBrowser profiles
Write-Output "Restoring ixBrowser profiles..."
$ixDest = "C:\Users\Administrator\AppData\Roaming\ixBrowser"
$ixBackup = Join-Path $backupTemp "ixBrowser"
if (Test-Path $ixBackup) {
    if (Test-Path $ixDest) {
        Remove-Item -Path $ixDest -Recurse -Force -ErrorAction SilentlyContinue
    }
    $ixDestParent = Split-Path $ixDest
    if (-not (Test-Path $ixDestParent)) {
        New-Item -ItemType Directory -Path $ixDestParent -Force | Out-Null
    }
    Copy-Item -Path "$ixBackup" -Destination "$ixDest" -Recurse -Force
    Write-Output "ixBrowser profiles restored."
}

# 3.5 Restore local Chrome profiles
Write-Output "Restoring local Chrome profiles..."
$chromeDest = Join-Path $monorepoDir "data\browser_profiles"
$chromeBackup = Join-Path $backupTemp "chrome_profiles"
if (Test-Path $chromeBackup) {
    if (Test-Path $chromeDest) {
        Remove-Item -Path $chromeDest -Recurse -Force -ErrorAction SilentlyContinue
    }
    $chromeDestParent = Split-Path $chromeDest
    if (-not (Test-Path $chromeDestParent)) {
        New-Item -ItemType Directory -Path $chromeDestParent -Force | Out-Null
    }
    Copy-Item -Path "$chromeBackup" -Destination "$chromeDest" -Recurse -Force
    Write-Output "Local Chrome profiles restored."
}

# 4. Restore Logs
Write-Output "Restoring logs..."
$logBackup = Join-Path $backupTemp "logs"
if (Test-Path $logBackup) {
    if (-not (Test-Path "$monorepoDir\logs")) {
        New-Item -ItemType Directory -Path "$monorepoDir\logs" -Force | Out-Null
    }
    Copy-Item -Path "$logBackup\*" -Destination "$monorepoDir\logs" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "Logs restored."
}

# 5. Restore MongoDB
Write-Output "Restoring MongoDB databases..."
$dumpPath = Join-Path $backupTemp "mongo"
if (Test-Path $dumpPath) {
    $mongorestore = Get-Command "mongorestore.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if (-not $mongorestore) {
        $paths = @(
            "C:\Program Files\MongoDB\Tools\100\bin\mongorestore.exe",
            "C:\Program Files\MongoDB\Server\7.0\bin\mongorestore.exe",
            "C:\Program Files\MongoDB\Server\6.0\bin\mongorestore.exe"
        )
        foreach ($p in $paths) {
            if (Test-Path $p) { $mongorestore = $p; break }
        }
    }

    if ($mongorestore) {
        & $mongorestore "$dumpPath"
        Write-Output "MongoDB restore finished."
    } else {
        Write-Warning "mongorestore.exe not found! Skipping database restore."
    }
}

# Clean up
Remove-Item -Path $backupTemp -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path $zipPath) {
    Remove-Item -Path $zipPath -Force
}
Write-Output "Restore complete!"
