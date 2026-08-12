# Backup MongoDB, logs, master CSVs, session_registry, and ixBrowser cookies to S3.
# Also publishes standalone ixbrowser_session.zip + session_registry.json for the next deploy.

$bucket = "jobbots-tfstate-bucket"
$installDir = "C:\automation"
$monorepoDir = "C:\automation\automation_monorepo"
$masterDir = "C:\automation\master"
$backupTemp = "C:\Windows\Temp\backup"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

Write-Output "Starting Backup to S3 ($timestamp)..."

function Invoke-Aws {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & aws @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "AWS CLI failed with exit code ${LASTEXITCODE}: aws $($Arguments -join ' ')"
    }
}

if (Test-Path $backupTemp) {
    Remove-Item -Path $backupTemp -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $backupTemp -Force | Out-Null

# 1. MongoDB
Write-Output "Dumping MongoDB databases..."
$dumpPath = Join-Path $backupTemp "mongo"
$mongodump = Get-Command "mongodump.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $mongodump) {
    foreach ($p in @(
        "C:\Program Files\MongoDB\Tools\100\bin\mongodump.exe",
        "C:\Program Files\MongoDB\Server\7.0\bin\mongodump.exe",
        "C:\Program Files\MongoDB\Server\6.0\bin\mongodump.exe"
    )) {
        if (Test-Path $p) { $mongodump = $p; break }
    }
}
if ($mongodump) {
    & $mongodump --out="$dumpPath"
    Write-Output "MongoDB dump finished."
} else {
    Write-Warning "mongodump.exe not found - skipping database backup."
}

# 2. Monorepo logs
Write-Output "Copying monorepo logs..."
$logBackup = Join-Path $backupTemp "logs_monorepo"
New-Item -ItemType Directory -Path $logBackup -Force | Out-Null
if (Test-Path "$monorepoDir\logs") {
    Copy-Item -Path "$monorepoDir\logs\*" -Destination $logBackup -Recurse -Force -ErrorAction SilentlyContinue
}

# 3. Master bot logs + all CSV histories
Write-Output "Copying master logs and all CSV histories..."
$masterBackup = Join-Path $backupTemp "master"
New-Item -ItemType Directory -Path $masterBackup -Force | Out-Null
if (Test-Path $masterDir) {
    Get-ChildItem -Path $masterDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Extension -eq ".csv" -or $_.Name -in @("log.txt", "indeed_log.txt", "glassdoor_log.txt")
    } | ForEach-Object {
        $relative = $_.FullName.Substring($masterDir.Length).TrimStart("\")
        $dest = Join-Path $masterBackup $relative
        $parent = Split-Path $dest -Parent
        if (-not (Test-Path $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -Path $_.FullName -Destination $dest -Force -ErrorAction SilentlyContinue
    }
    Write-Output "Master artifacts copied."
} else {
    Write-Warning "master directory not found at $masterDir"
}

# 4. Monorepo CSV histories + supervisor runtime state
Write-Output "Copying monorepo CSV histories and supervisor runtime data..."
$runtimeBackup = Join-Path $backupTemp "runtime"
New-Item -ItemType Directory -Path $runtimeBackup -Force | Out-Null
Get-ChildItem -Path $monorepoDir -Recurse -File -Filter "*.csv" -ErrorAction SilentlyContinue | ForEach-Object {
    $relative = $_.FullName.Substring($monorepoDir.Length).TrimStart("\")
    $dest = Join-Path $runtimeBackup $relative
    $parent = Split-Path $dest -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -Path $_.FullName -Destination $dest -Force
}
if (Test-Path "$monorepoDir\data\supervisor") {
    Copy-Item -Path "$monorepoDir\data\supervisor" -Destination $runtimeBackup -Recurse -Force
}

# 5. session_registry.json
Write-Output "Copying session_registry.json..."
$registrySrc = Join-Path $monorepoDir "data\supervisor\session_registry.json"
if (Test-Path $registrySrc) {
    Copy-Item -Path $registrySrc -Destination (Join-Path $backupTemp "session_registry.json") -Force
    Write-Output "session_registry.json copied."
} else {
    Write-Warning "session_registry.json not found - supervisor gating may be empty on next deploy."
}

# 6. ixBrowser profiles (legacy cookies) - exclude cache dirs
Write-Output "Copying ixBrowser profiles..."
$ixBackup = Join-Path $backupTemp "ixBrowser"
New-Item -ItemType Directory -Path $ixBackup -Force | Out-Null
$ixSrc = "C:\Users\Administrator\AppData\Roaming\ixBrowser"
if (Test-Path $ixSrc) {
    Get-ChildItem -Path $ixSrc -Recurse -ErrorAction SilentlyContinue | Where-Object {
        $_.FullName -notmatch "Cache" -and $_.FullName -notmatch "cache" -and $_.FullName -notmatch "\\logs\\"
    } | ForEach-Object {
        $relative = $_.FullName.Substring($ixSrc.Length).TrimStart("\")
        if ([string]::IsNullOrEmpty($relative)) { return }
        $dest = Join-Path $ixBackup $relative
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
        } else {
            $parent = Split-Path $dest -Parent
            if (-not (Test-Path $parent)) {
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
            }
            Copy-Item -Path $_.FullName -Destination $dest -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Output "ixBrowser profiles copied."
} else {
    Write-Warning "ixBrowser AppData directory not found."
}

# 6.5 Local Chrome profiles - exclude cache/temporary dirs
Write-Output "Copying local Chrome profiles..."
$chromeBackup = Join-Path $backupTemp "chrome_profiles"
New-Item -ItemType Directory -Path $chromeBackup -Force | Out-Null
$chromeSrc = Join-Path $monorepoDir "data\browser_profiles"
if (Test-Path $chromeSrc) {
    Get-ChildItem -Path $chromeSrc -Recurse -ErrorAction SilentlyContinue | Where-Object {
        $_.FullName -notmatch "Cache" -and $_.FullName -notmatch "cache" -and $_.FullName -notmatch "Code Cache" -and $_.FullName -notmatch "GPUCache" -and $_.FullName -notmatch "ShaderCache" -and $_.FullName -notmatch "Service Worker"
    } | ForEach-Object {
        $relative = $_.FullName.Substring($chromeSrc.Length).TrimStart("\")
        if ([string]::IsNullOrEmpty($relative)) { return }
        $dest = Join-Path $chromeBackup $relative
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
        } else {
            $parent = Split-Path $dest -Parent
            if (-not (Test-Path $parent)) {
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
            }
            Copy-Item -Path $_.FullName -Destination $dest -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Output "Local Chrome profiles copied."
} else {
    Write-Warning "Local Chrome profiles directory not found."
}

# 7. Full backup zip
Write-Output "Zipping full backup..."
$zipPath = Join-Path $installDir "latest_backup.zip"
if (Test-Path $zipPath) { Remove-Item -Path $zipPath -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($backupTemp, $zipPath)

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

Write-Output "Uploading latest_backup.zip ..."
Invoke-Aws s3 cp "$zipPath" "s3://$bucket/backups/latest_backup.zip"
Invoke-Aws s3 cp "$zipPath" "s3://$bucket/backups/archive/backup-$timestamp.zip"

# 8. Standalone session zip for next deploy (legacy ixBrowser only)
Write-Output "Publishing ixbrowser_session.zip for next deploy..."
$sessionZip = Join-Path $installDir "ixbrowser_session.zip"
if (Test-Path $sessionZip) { Remove-Item -Path $sessionZip -Force }
if (Test-Path $ixSrc) {
    Compress-Archive -Path $ixSrc -DestinationPath $sessionZip -Force
    Invoke-Aws s3 cp "$sessionZip" "s3://$bucket/backups/ixbrowser_session.zip"
    Write-Output "ixbrowser_session.zip uploaded."
} else {
    Write-Warning "Skipped ixbrowser_session.zip - no ixBrowser profile dir."
}

# 8.5 Standalone local Chrome session zip for next deploy
Write-Output "Publishing chrome_profiles.zip for next deploy..."
$chromeZip = Join-Path $installDir "chrome_profiles.zip"
if (Test-Path $chromeZip) { Remove-Item -Path $chromeZip -Force }
if (Test-Path $chromeSrc) {
    Compress-Archive -Path "$chromeBackup\*" -DestinationPath $chromeZip -Force
    Invoke-Aws s3 cp "$chromeZip" "s3://$bucket/backups/chrome_profiles.zip"
    Write-Output "chrome_profiles.zip uploaded."
} else {
    Write-Warning "Skipped chrome_profiles.zip - no local Chrome profiles dir."
}

# 9. Standalone session_registry for next deploy
if (Test-Path $registrySrc) {
    Write-Output "Publishing session_registry.json ..."
    Invoke-Aws s3 cp $registrySrc "s3://$bucket/backups/session_registry.json"
}

Write-Output "Backup upload to S3 complete ($timestamp)."

Remove-Item -Path $backupTemp -Recurse -Force -ErrorAction SilentlyContinue
