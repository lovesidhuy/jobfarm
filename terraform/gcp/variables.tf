variable "gcp_project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "gcp_region" {
  description = "GCP region for VM deployment"
  type        = string
  default     = "us-west1"
}

variable "gcp_zone" {
  description = "GCP zone for VM deployment"
  type        = string
  default     = "us-west1-a"
}

variable "environment" {
  description = "Deployment environment tag"
  type        = string
  default     = "canary"
}

variable "deployment_tier" {
  description = "Protected deployment tier"
  type        = string
  default     = "canary"

  validation {
    condition     = contains(["canary", "production"], var.deployment_tier)
    error_message = "deployment_tier must be canary or production."
  }
}

variable "resource_prefix" {
  description = "Unique prefix applied to every worker resource"
  type        = string
  default     = "jobbots-canary"

  validation {
    condition     = length(var.resource_prefix) >= 8 && can(regex("^[a-z0-9-]+$", var.resource_prefix))
    error_message = "resource_prefix must be at least 8 lowercase alphanumeric/hyphen characters."
  }
}

variable "vm_name" {
  description = "Name of the ephemeral GCP compute instance"
  type        = string
  default     = "jobbots-dev-vm"
}

variable "machine_type" {
  description = "GCP Compute Engine machine type"
  type        = string
  default     = "e2-standard-4"
}

variable "boot_disk_size_gb" {
  description = "Size of the root boot disk in GB"
  type        = number
  default     = 64
}

variable "golden_image_family" {
  description = "GCP Image family or specific image link. Defaults to Ubuntu 24.04 LTS."
  type        = string
  default     = "ubuntu-2404-lts-amd64"
}

variable "golden_image_project" {
  description = "GCP project owning the boot image"
  type        = string
  default     = "ubuntu-os-cloud"
}

variable "allowed_rdp_ip_v4" {
  description = "Operator IPv4 CIDR allowed to connect via RDP (port 3389). Empty disables public RDP."
  type        = string
  default     = ""
}

variable "allowed_rdp_ip_v6" {
  description = "Operator IPv6 CIDR allowed to connect via RDP (port 3389). Empty disables public RDP."
  type        = string
  default     = ""
}

variable "vm_admin_password" {
  description = "Password for the ubuntu user (xrdp login)."
  type        = string
  sensitive   = true
  default     = ""
}

# AWS backend integration variables
variable "aws_region" {
  description = "AWS region for backend services (S3, DynamoDB, SecretsManager)"
  type        = string
  default     = "us-west-2"
}

variable "aws_access_key_id" {
  description = "AWS Access Key ID for GCP VM to access AWS backend services"
  type        = string
  sensitive   = true
  default     = ""
}

variable "aws_secret_access_key" {
  description = "AWS Secret Access Key for GCP VM to access AWS backend services"
  type        = string
  sensitive   = true
  default     = ""
}

variable "profile_lease_table_name" {
  description = "DynamoDB profile lease table name on AWS"
  type        = string
  default     = "jobbots-canary-profile-leases"
}

variable "artifact_bucket_name" {
  description = "S3 artifact bucket name on AWS"
  type        = string
  default     = "jobbots-canary-artifacts"
}

variable "artifact_prefix" {
  description = "S3 prefix reserved for this environment"
  type        = string
  default     = "canary"
}

variable "runtime_secret_name" {
  description = "Secrets Manager runtime secret name on AWS"
  type        = string
  default     = "jobbots-canary/runtime"
}
