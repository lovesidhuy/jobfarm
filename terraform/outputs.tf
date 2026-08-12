output "vm_id" {
  description = "AWS EC2 instance ID of the ephemeral Linux worker"
  value       = aws_instance.automation_vm.id
}

output "vm_private_ip" {
  description = "Private IP address of the Linux worker"
  value       = aws_instance.automation_vm.private_ip
}

output "vm_public_ip" {
  description = "Public IP address of the Linux worker"
  value       = aws_instance.automation_vm.public_ip
}

output "rdp_connection_string" {
  description = "Direct RDP connection string when allowed_rdp_ip_* is configured"
  value       = var.allowed_rdp_ip_v4 != "" || var.allowed_rdp_ip_v6 != "" ? "${aws_instance.automation_vm.public_ip}:3389" : "RDP disabled — set allowed_rdp_ip_v4 or use SSM port forwarding"
}

output "rdp_username" {
  description = "Default xrdp login user"
  value       = "ubuntu"
}

output "persistent_volume_id" {
  description = "Attached EBS volume that survives worker destruction"
  value       = data.aws_ebs_volume.persistent.id
}

output "ssm_novnc_tunnel_command" {
  description = "Open a local tunnel to noVNC after its service is configured"
  value       = "aws ssm start-session --target ${aws_instance.automation_vm.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"6080\"],\"localPortNumber\":[\"6080\"]}'"
}
