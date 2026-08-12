# Kill all Chrome and chromedriver processes, clear stale profile locks.
# Run before starting the supervisor if bots fail to launch / show extra windows.

Write-Host "Killing Chrome and chromedriver processes..."
Get-Process chrome -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process chromedriver -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process undetected_chromedriver -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 2

Write-Host "Removing stale profile locks..."
$ProfileBase = "C:\automation\profiles"
if (Test-Path $ProfileBase) {
    Get-ChildItem $ProfileBase -Directory | ForEach-Object {
        foreach ($file in @("SingletonLock", "SingletonCookie", "SingletonSocket")) {
            $p = Join-Path $_.FullName $file
            if (Test-Path $p) {
                Remove-Item $p -Force -ErrorAction SilentlyContinue
                Write-Host "  removed $p"
            }
        }
    }
}

Write-Host "Done. Safe to start supervisor."
