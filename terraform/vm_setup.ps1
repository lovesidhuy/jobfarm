# VM Setup Script for Windows Server - Automation Bots
# Run this script in PowerShell as Administrator on the Azure VM

Write-Host "Starting VM Setup for Automation Bots..." -ForegroundColor Green

# 1. Enable PowerShell execution policy
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force

# 2. Install Chocolatey
Write-Host "Installing Chocolatey..." -ForegroundColor Yellow
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# 3. Install Python 3.11
Write-Host "Installing Python 3.11..." -ForegroundColor Yellow
choco install python311 -y

# 4. Install Git
Write-Host "Installing Git..." -ForegroundColor Yellow
choco install git -y

# 5. Install Google Chrome
Write-Host "Installing Google Chrome..." -ForegroundColor Yellow
choco install googlechrome -y

# 6. Install MongoDB
Write-Host "Installing MongoDB..." -ForegroundColor Yellow
choco install mongodb -y

# 7. Install Visual Studio Build Tools (for some Python packages)
Write-Host "Installing Visual Studio Build Tools..." -ForegroundColor Yellow
choco install visualstudio2022buildtools --package-parameters "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" -y

# 8. Create automation directory
Write-Host "Creating automation directory..." -ForegroundColor Yellow
New-Item -Path "C:\automation" -ItemType Directory -Force
Set-Location -Path "C:\automation"

# 9. Configure MongoDB for remote access
Write-Host "Configuring MongoDB..." -ForegroundColor Yellow
$mongodConfig = @"
systemLog:
  destination: file
  logAppend: true
  path:  C:\data\log\mongod.log
storage:
  dbPath: C:\data\db
net:
  bindIp: 0.0.0.0
  port: 27017
"@

# Create MongoDB directories
New-Item -Path "C:\data\db" -ItemType Directory -Force
New-Item -Path "C:\data\log" -ItemType Directory -Force

# Write MongoDB config
$mongodConfig | Out-File -FilePath "C:\Program Files\MongoDB\Server\7.0\bin\mongod.cfg" -Encoding UTF8 -Force

# 10. Start MongoDB service
Write-Host "Starting MongoDB service..." -ForegroundColor Yellow
Start-Service -Name "MongoDB"
Set-Service -Name "MongoDB" -StartupType Automatic

# 11. Install Ollama
Write-Host "Installing Ollama..." -ForegroundColor Yellow
Invoke-WebRequest -Uri "https://ollama.ai/install.ps1" -OutFile "install_ollama.ps1"
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
.\install_ollama.ps1

# 12. Pull Ollama models
Write-Host "Pulling Ollama models (this may take a while)..." -ForegroundColor Yellow
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:7b

# 13. Start Ollama service
Write-Host "Starting Ollama service..." -ForegroundColor Yellow
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "4", "Machine")
$env:OLLAMA_NUM_PARALLEL = "4"
Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden

# 14. Refresh PATH
Write-Host "Refreshing environment variables..." -ForegroundColor Yellow
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")

# 15. Install Python dependencies
Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
# Wait for Python to be available
do {
    Start-Sleep -Seconds 5
    $python = Get-Command python -ErrorAction SilentlyContinue
} while (-not $python)

# Upgrade pip
python -m pip install --upgrade pip

# Install requirements (will create requirements.txt later)
Write-Host "Python is ready. Next steps:" -ForegroundColor Green
Write-Host "1. Clone your repository: git clone <your-repo-url> ." -ForegroundColor Cyan
Write-Host "2. Copy requirements.txt to C:\automation" -ForegroundColor Cyan
Write-Host "3. Run: pip install -r requirements.txt" -ForegroundColor Cyan
Write-Host "4. Install Playwright browsers: playwright install chromium" -ForegroundColor Cyan

# 16. Configure Windows Firewall
Write-Host "Configuring Windows Firewall..." -ForegroundColor Yellow
# Allow Ollama
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -Port 11434 -Protocol TCP -Action Allow
# Allow MongoDB (restricted to your IP later)
New-NetFirewallRule -DisplayName "MongoDB" -Direction Inbound -Port 27017 -Protocol TCP -Action Allow
# Allow Chrome CDP ports
New-NetFirewallRule -DisplayName "Chrome CDP" -Direction Inbound -Port 9222-9228 -Protocol TCP -Action Allow

# 17. Create startup script for Ollama
$ollamaStartup = @"
@echo off
cd /d C:\automation
set OLLAMA_NUM_PARALLEL=4
ollama serve
"@
$ollamaStartup | Out-File -FilePath "C:\automation\start_ollama.bat" -Encoding ASCII -Force

# 18. Create environment variables file
$envFile = @"
# Automation Environment Variables
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_NUM_PARALLEL=4
MONGO_URI=mongodb://localhost:27017/
GROQ_API_KEY=your-groq-api-key-here
INDEED_BASE_URL=https://ca.indeed.com
GLASSDOOR_BASE_URL=https://www.glassdoor.ca
"@
$envFile | Out-File -FilePath "C:\automation\.env" -Encoding UTF8 -Force

Write-Host "VM Setup completed!" -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Reboot the VM to ensure all services start properly" -ForegroundColor Cyan
Write-Host "2. RDP back in and continue with project deployment" -ForegroundColor Cyan
Write-Host "3. Clone your repository and install dependencies" -ForegroundColor Cyan
Write-Host "4. Update configuration files with VM IP address" -ForegroundColor Cyan

# Ask for reboot
$reboot = Read-Host "Do you want to reboot now? (y/n)"
if ($reboot -eq 'y') {
    Restart-Computer -Force
}
