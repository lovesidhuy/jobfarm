# Docker Setup Script for Windows Server - Automation Bots
# Run this script in PowerShell as Administrator on the Azure VM

Write-Host "🐳 Starting Docker Setup for Automation Bots..." -ForegroundColor Green

# 1. Enable Hyper-V and Containers
Write-Host "Enabling Hyper-V and Containers..." -ForegroundColor Yellow
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All -NoRestart
Enable-WindowsOptionalFeature -Online -FeatureName Containers -All -NoRestart

# 2. Install Chocolatey if not present
if (!(Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Chocolatey..." -ForegroundColor Yellow
    Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
}

# 3. Install Docker Desktop
Write-Host "Installing Docker Desktop..." -ForegroundColor Yellow
choco install docker-desktop -y

# 4. Create automation directory
Write-Host "Creating automation directory..." -ForegroundColor Yellow
New-Item -Path "C:\automation" -ItemType Directory -Force
Set-Location -Path "C:\automation"

# 5. Configure Docker to start on boot
Write-Host "Configuring Docker startup..." -ForegroundColor Yellow
Set-Service -Name "com.docker.service" -StartupType Automatic

# 6. Configure firewall for Docker
Write-Host "Configuring firewall for Docker..." -ForegroundColor Yellow
New-NetFirewallRule -DisplayName "Docker" -Direction Inbound -Port 2375-2376 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "Docker Swarm" -Direction Inbound -Port 2377 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "Docker Overlay" -Direction Inbound -Port 4789 -Protocol UDP -Action Allow
New-NetFirewallRule -DisplayName "Docker VXLAN" -Direction Inbound -Port 7946 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "Docker VXLAN UDP" -Direction Inbound -Port 7946 -Protocol UDP -Action Allow

# 7. Create environment file
Write-Host "Creating environment configuration..." -ForegroundColor Yellow
@"
# Automation Environment Variables
GROQ_API_KEY=your-groq-api-key-here
COMPOSE_PROJECT_NAME=automation
OLLAMA_NUM_PARALLEL=4
"@
Out-File -FilePath ".env" -Encoding UTF8 -Force

# 8. Create utility scripts
Write-Host "Creating utility scripts..." -ForegroundColor Yellow

# Start services script
@"
@echo off
echo Starting Docker services...
docker-compose up -d mongodb ollama

echo Waiting for services to be ready...
timeout /t 30

echo Pulling Ollama models...
docker exec automation-ollama ollama pull llama3.2:3b
docker exec automation-ollama ollama pull qwen2.5-coder:7b

echo Services started!
echo MongoDB: mongodb://admin:changeme@localhost:27017/
echo Ollama: http://localhost:11434
"@ | Out-File -FilePath "start-services.bat" -Encoding ASCII -Force

# Start bots script
@"
@echo off
echo Starting all automation bots...
docker-compose --profile bots up -d

echo Bots started! Check status with:
docker-compose ps
"@ | Out-File -FilePath "start-bots.bat" -Encoding ASCII -Force

# Stop all script
@"
@echo off
echo Stopping all services...
docker-compose down

echo All services stopped!
"@ | Out-File -FilePath "stop-all.bat" -Encoding ASCII -Force

# Monitor script
@"
@echo off
echo Monitoring automation bots...
docker-compose logs -f
"@ | Out-File -FilePath "monitor.bat" -Encoding ASCII -Force

# Status script
@"
@echo off
echo Checking service status...
docker-compose ps

echo ""
echo "Service URLs:"
echo "MongoDB: mongodb://admin:changeme@localhost:27017/"
echo "Ollama: http://localhost:11434"
echo "Monitoring: http://localhost:8080 (if enabled)"
"@ | Out-File -FilePath "status.bat" -Encoding ASCII -Force

Write-Host "Docker setup completed!" -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Restart the VM to complete Hyper-V installation" -ForegroundColor White
Write-Host "2. Start Docker Desktop" -ForegroundColor White
Write-Host "3. Copy your automation project to C:\automation\" -ForegroundColor White
Write-Host "4. Run: start-services.bat" -ForegroundColor White
Write-Host "5. Run: start-bots.bat" -ForegroundColor White
Write-Host "" -ForegroundColor White
Write-Host "Available commands:" -ForegroundColor Yellow
Write-Host "- start-services.bat  Start MongoDB and Ollama" -ForegroundColor White
Write-Host "- start-bots.bat      Start all automation bots" -ForegroundColor White
Write-Host "- stop-all.bat        Stop all services" -ForegroundColor White
Write-Host "- monitor.bat         View live logs" -ForegroundColor White
Write-Host "- status.bat          Check service status" -ForegroundColor White

$restart = Read-Host "Do you want to restart now to complete Hyper-V installation? (y/n)"
if ($restart -eq 'y') {
    Restart-Computer -Force
}
