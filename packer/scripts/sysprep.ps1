# sysprep.ps1 === Generalize Windows for AWS EC2 AMI capture
$ErrorActionPreference = "Stop"

Write-Output "====================================================================================================================================================================="
Write-Output " Running EC2Launch Sysprep === generalizing image"
Write-Output "====================================================================================================================================================================="

# Run EC2Launch v2 sysprep command without shutting down immediately, allowing Packer to complete the run
& "C:\Program Files\Amazon\EC2Launch\ec2launch.exe" sysprep --shutdown=false

Write-Output "EC2Launch Sysprep complete === image ready for capture"
