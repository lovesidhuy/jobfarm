terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # Keep compute state in the existing low-cost AWS Terraform state bucket so
  # GCP remains the compute layer and AWS remains the persistence layer.
  backend "s3" {}
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
  zone    = var.gcp_zone
}

# Network setup
resource "google_compute_network" "automation" {
  name                    = "${var.resource_prefix}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "worker" {
  name          = "${var.resource_prefix}-subnet"
  ip_cidr_range = "10.10.1.0/24"
  region        = var.gcp_region
  network       = google_compute_network.automation.id
}

# Firewall rule allowing RDP (3389) from operator IP
resource "google_compute_firewall" "allow_rdp" {
  count   = (var.allowed_rdp_ip_v4 != "" || var.allowed_rdp_ip_v6 != "") ? 1 : 0
  name    = "${var.resource_prefix}-allow-rdp"
  network = google_compute_network.automation.name

  allow {
    protocol = "tcp"
    ports    = ["3389"]
  }

  source_ranges = compact([
    var.allowed_rdp_ip_v4 != "" ? var.allowed_rdp_ip_v4 : "",
    var.allowed_rdp_ip_v6 != "" ? var.allowed_rdp_ip_v6 : ""
  ])
  target_tags = ["jobbots-worker"]
}

# Firewall rule allowing egress
resource "google_compute_firewall" "allow_egress" {
  name      = "${var.resource_prefix}-allow-egress"
  network   = google_compute_network.automation.name
  direction = "EGRESS"

  allow {
    protocol = "all"
  }

  destination_ranges = ["0.0.0.0/0"]
}

# Lifecycle SSH/SCP uses IAP, so the worker never needs port 22 open to the
# public internet. The deployer service account is granted IAP tunnel access
# separately from network administration.
resource "google_compute_firewall" "allow_iap_ssh" {
  name    = "${var.resource_prefix}-allow-iap-ssh"
  network = google_compute_network.automation.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["jobbots-worker"]
}

# Reserve external IP address for the VM
resource "google_compute_address" "worker_ip" {
  name   = "${var.resource_prefix}-ip"
  region = var.gcp_region
}

# Compute Engine Instance
resource "google_compute_instance" "automation_vm" {
  name         = var.vm_name
  machine_type = var.machine_type
  zone         = var.gcp_zone

  tags = ["jobbots-worker", "ephemeral"]

  boot_disk {
    initialize_params {
      image = "projects/${var.golden_image_project}/global/images/family/${var.golden_image_family}"
      size  = var.boot_disk_size_gb
      type  = "pd-ssd"
    }
  }

  network_interface {
    network    = google_compute_network.automation.name
    subnetwork = google_compute_subnetwork.worker.name

    access_config {
      nat_ip = google_compute_address.worker_ip.address
    }
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = templatefile("${path.module}/startup-script.sh.tftpl", {
    gcp_project_id        = var.gcp_project_id
    gcp_region            = var.gcp_region
    gcp_zone              = var.gcp_zone
    aws_region            = var.aws_region
    aws_access_key_id     = var.aws_access_key_id
    aws_secret_access_key = var.aws_secret_access_key
    profile_lease_table   = var.profile_lease_table_name
    artifact_bucket       = var.artifact_bucket_name
    artifact_prefix       = var.artifact_prefix
    runtime_secret_name   = var.runtime_secret_name
    deployment_tier       = var.deployment_tier
    resource_prefix       = var.resource_prefix
    vm_admin_password     = var.vm_admin_password
  })

  labels = {
    environment     = var.environment
    deployment_tier = var.deployment_tier
    resource_prefix = var.resource_prefix
    project         = "jobbots"
    managed_by      = "terraform"
  }

  lifecycle {
    precondition {
      condition     = var.deployment_tier != "canary" || strcontains(var.resource_prefix, "canary")
      error_message = "Canary workers require a resource_prefix containing 'canary'."
    }
  }
}
