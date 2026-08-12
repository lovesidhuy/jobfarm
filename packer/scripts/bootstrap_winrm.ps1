<powershell>
# Enable WinRM HTTP listener on port 5985
winrm quickconfig -q
# Allow unencrypted messages
winrm set winrm/config/service '@{AllowUnencrypted="true"}'
# Allow basic authentication
winrm set winrm/config/service/auth '@{Basic="true"}'
# Configure WinRM listener to start automatically
Set-Service WinRM -StartupType Automatic
# Open firewall port 5985 for WinRM
netsh advfirewall firewall add rule name="WinRM-HTTP" dir=in action=allow protocol=TCP localport=5985
# Restart WinRM service to apply settings
Restart-Service WinRM
</powershell>
