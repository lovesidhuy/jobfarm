output "vm_id" {
  description = "GCP Compute Engine instance ID"
  value       = google_compute_instance.automation_vm.instance_id
}

output "vm_name" {
  description = "GCP Compute Engine instance name"
  value       = google_compute_instance.automation_vm.name
}

output "vm_private_ip" {
  description = "Private IP address of the GCP worker"
  value       = google_compute_instance.automation_vm.network_interface[0].network_ip
}

output "vm_public_ip" {
  description = "Public IP address of the GCP worker"
  value       = google_compute_address.worker_ip.address
}

output "rdp_connection_string" {
  description = "Direct RDP connection string when allowed_rdp_ip_* is configured"
  value       = var.allowed_rdp_ip_v4 != "" || var.allowed_rdp_ip_v6 != "" ? "${google_compute_address.worker_ip.address}:3389" : "RDP disabled — set allowed_rdp_ip_v4 or enable firewall"
}

output "rdp_username" {
  description = "Default xrdp login user"
  value       = "ubuntu"
}

output "gcloud_ssh_command" {
  description = "Command to SSH into the GCP instance via gcloud"
  value       = "gcloud compute ssh --zone=${var.gcp_zone} ${var.vm_name} --project=${var.gcp_project_id}"
}
