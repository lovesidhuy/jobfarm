# provision.ps1 === Packer provisioner for jobbots golden image
# ====================================================================================================================================================================================================================================
# Installs all heavy software that rarely changes. Extracted from the Ansible
# playbook.yml install tasks so they only run ONCE during image build, not on
# every deploy.
#
# What's baked in:  Chocolatey, Python 3.11, Git, MongoDB, NodeJS, Google Chrome,
#                   OpenSSH, pip packages, Playwright, WinRM, firewall rules,
#                   RDP multi-session, directory structure.
#
# What Ansible still handles at deploy time:
#   - Clone/update repo, write .env secrets, desktop shortcuts,
#     ixBrowser setup, pip install (for new packages since image build).
# ====================================================================================================================================================================================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # suppress progress bars (faster downloads)

Write-Output "====================================================================================================================================================================="
Write-Output " Jobbots Golden Image Provisioner"
Write-Output "====================================================================================================================================================================="

# ========= 1. Chocolatey ==============================================================================================================================================================================

Write-Output "`n--- Installing Chocolatey ---"
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = `
    [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
$chocoInstall = 'C:\Windows\Temp\choco_install.ps1'
Invoke-WebRequest -Uri 'https://community.chocolatey.org/install.ps1' `
    -OutFile $chocoInstall -UseBasicParsing
$chocoProc = Start-Process powershell.exe `
    -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $chocoInstall `
    -Wait -PassThru -NoNewWindow
if ($chocoProc.ExitCode -ne 0) {
    throw "Chocolatey installer failed with exit code $($chocoProc.ExitCode)"
}
# Wait for pending file locks to clear (chocolatey post-install)
Start-Sleep -Seconds 15
$chocoExe = 'C:\ProgramData\chocolatey\bin\choco.exe'
if (-not (Test-Path $chocoExe)) {
    throw "Chocolatey install completed but $chocoExe is missing"
}
$env:Path = 'C:\ProgramData\chocolatey\bin;' +
            [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')
Write-Output "Chocolatey installed: $(& $chocoExe --version)"

# ========= 2. Python 3.11, Git, MongoDB, NodeJS ======================================================================================================

Write-Output "`n--- Installing Python 3.11, Git, MongoDB, NodeJS via Chocolatey ---"
$maxAttempts = 2
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    Write-Output "Attempt $attempt/$maxAttempts..."
    $chocoOutput = & $chocoExe install python311 git mongodb nodejs -y --no-progress --timeout 3600 2>&1
    Write-Output $chocoOutput
    if ($LASTEXITCODE -eq 0) { break }
    if ($attempt -eq $maxAttempts) {
        throw "Chocolatey install failed after $maxAttempts attempts"
    }
    Write-Output "Retrying in 10s..."
    Start-Sleep -Seconds 10
}

# ========= 3. Google Chrome ==================================================================================================================================================================

Write-Output "`n--- Installing Google Chrome ---"
$chromeMsi = 'C:\Windows\Temp\chrome.msi'
Invoke-WebRequest -Uri `
    'https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi' `
    -OutFile $chromeMsi -UseBasicParsing
Start-Process msiexec.exe -ArgumentList "/i `"$chromeMsi`" /quiet /norestart" -Wait -NoNewWindow
Write-Output "Chrome installed."

# ========= 4. MongoDB service ============================================================================================================================================================

Write-Output "`n--- Configuring MongoDB service ---"
Set-Service -Name MongoDB -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name MongoDB -ErrorAction SilentlyContinue
Write-Output "MongoDB service: $((Get-Service MongoDB -ErrorAction SilentlyContinue).Status)"

# ========= 5. RDP multi-session ======================================================================================================================================================

Write-Output "`n--- Configuring RDP multi-session ---"
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' `
    -Name fSingleSessionPerUser -Value 0 -Type DWord
$tsPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'
New-Item -Path $tsPath -Force | Out-Null
Set-ItemProperty -Path $tsPath -Name fSingleSessionPerUser -Value 0 -Type DWord
Set-ItemProperty -Path $tsPath -Name MaxInstanceCount -Value 10 -Type DWord
Write-Output "RDP multi-session enabled (up to 10 concurrent sessions)."

# ========= 6. OpenSSH Server ===============================================================================================================================================================

Write-Output "`n--- Installing OpenSSH Server ---"
try {
    $sshd = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
    if ($sshd -and $sshd.State -ne 'Installed') {
        Add-WindowsCapability -Online -Name $sshd.Name
        Write-Output "OpenSSH Server installed."
    } elseif ($sshd) {
        Write-Output "OpenSSH Server already installed."
    } else {
        Write-Output "WARNING: OpenSSH Server capability not found."
    }

    $sshdService = Get-Service sshd -ErrorAction SilentlyContinue
    if ($sshdService) {
        Set-Service -Name sshd -StartupType Automatic
        Start-Service -Name sshd -ErrorAction SilentlyContinue
        Set-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' `
            -Name DefaultShell `
            -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
            -Type String
        Write-Output "sshd service: $((Get-Service sshd).Status)"
    } else {
        Write-Output "WARNING: sshd service unavailable; continuing without baked OpenSSH."
    }
} catch {
    Write-Output "WARNING: OpenSSH install/config failed; continuing without baked OpenSSH. $_"
}

# ========= 7. WinRM hardening ============================================================================================================================================================
# Same config as the Terraform CustomScriptExtension (azurerm_virtual_machine_extension).
# Baking it into the image means WinRM is ready immediately after boot.

Write-Output "`n--- Hardening WinRM ---"
winrm set winrm/config/service '@{AllowUnencrypted="true"}'
winrm set winrm/config/service/auth '@{Basic="true"}'
Set-Service WinRM -StartupType Automatic
Write-Output "WinRM hardened."

# ========= 8. Firewall rules ===============================================================================================================================================================

Write-Output "`n--- Configuring firewall rules ---"
# Only WinRM-HTTP and OpenSSH need inbound exposure on the OS firewall.
# MongoDB, ChromeCDP, and ixBrowser Local API are accessed locally/via loopback and do not need inbound rules.
$rules = @(
    @{ Name = 'OpenSSH-Server-In-TCP'; Port = 22 },
    @{ Name = 'WinRM-HTTP';            Port = 5985 }
)
foreach ($rule in $rules) {
    Remove-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $rule.Name `
        -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $rule.Port `
        -Profile Any -Enabled True | Out-Null
    Write-Output "  $($rule.Name): port $($rule.Port) open"
}
# Cleanup any old rules for local-only services
foreach ($name in @('MongoDB', 'ChromeCDP', 'AdsPower', 'ixBrowser-LocalAPI')) {
    Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
}

# ========= 9. Directory structure ================================================================================================================================================

Write-Output "`n--- Creating directory structure ---"
$dirs = @(
    'C:\automation',
    'C:\automation\automation_monorepo\logs',
    'C:\automation\automation_monorepo\data',
    'C:\automation\master\Auto_job_applier_linkedIn_gen\data',
    'C:\automation\master\Auto_job_applier_linkedIn_gen\logs',
    'C:\automation\master\Auto_job_applier_linkedIn_it\data',
    'C:\automation\master\Auto_job_applier_linkedIn_it\logs',
    'C:\automation\profiles'
)
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}
Write-Output "Created $($dirs.Count) directories under C:\automation."

# ========= 10. Refresh PATH + pip packages =====================================================================================================================

Write-Output "`n--- Installing Python packages ---"
$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')
python -m pip install --upgrade pip --no-cache-dir
python -m pip install -r 'C:\Windows\Temp\requirements.txt' --no-cache-dir
$pkgCount = (python -m pip list --format=columns 2>&1 | Measure-Object -Line).Lines - 2
Write-Output "Installed $pkgCount pip packages."

# ========= 11. Playwright Chromium =============================================================================================================================================

Write-Output "`n--- Installing Playwright Chromium ---"
$env:PLAYWRIGHT_BROWSERS_PATH = 'C:\ms-playwright'
[System.Environment]::SetEnvironmentVariable('PLAYWRIGHT_BROWSERS_PATH', $env:PLAYWRIGHT_BROWSERS_PATH, 'Machine')
New-Item -ItemType Directory -Path $env:PLAYWRIGHT_BROWSERS_PATH -Force | Out-Null
python -m playwright install chromium
Write-Output "Playwright Chromium installed."

# ========= Done =========================================================================================================================================================================================================

Write-Output "`n====================================================================================================================================================================="
Write-Output " Provisioning complete === ready for sysprep"
Write-Output "====================================================================================================================================================================="
