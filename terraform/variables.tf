variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-west-2"
}

variable "availability_zone" {
  description = "Availability zone shared by the ephemeral worker and persistent EBS volume"
  type        = string
  default     = "us-west-2a"
}

variable "environment" {
  description = "Deployment environment tag"
  type        = string
  default     = "canary"
}

variable "deployment_tier" {
  description = "Protected deployment tier"
  type        = string

  validation {
    condition     = contains(["canary", "production"], var.deployment_tier)
    error_message = "deployment_tier must be canary or production."
  }
}

variable "resource_prefix" {
  description = "Unique prefix applied to every worker resource"
  type        = string

  validation {
    condition     = length(var.resource_prefix) >= 8 && can(regex("^[a-z0-9-]+$", var.resource_prefix))
    error_message = "resource_prefix must be at least 8 lowercase alphanumeric/hyphen characters."
  }
}

variable "vm_name" {
  description = "Name of the ephemeral Linux worker"
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "Linux worker instance type"
  type        = string
  default     = "m6i.large"
}

variable "root_volume_size_gb" {
  description = "Disposable encrypted root volume size"
  type        = number
  default     = 64
}

variable "golden_image_id" {
  description = "Optional jobbots Linux golden AMI ID"
  type        = string
  default     = ""
}

variable "persistent_volume_id" {
  description = "Encrypted EBS volume ID produced by terraform/persistent"
  type        = string
}

variable "profile_lease_table_name" {
  description = "DynamoDB profile lease table name produced by terraform/persistent"
  type        = string
}

variable "profile_lease_table_arn" {
  description = "DynamoDB profile lease table ARN produced by terraform/persistent"
  type        = string
}

variable "artifact_bucket_name" {
  description = "Encrypted artifact bucket name produced by terraform/persistent"
  type        = string
}

variable "artifact_bucket_arn" {
  description = "Encrypted artifact bucket ARN produced by terraform/persistent"
  type        = string
}

variable "runtime_secret_name" {
  description = "Secrets Manager runtime secret name produced by terraform/persistent"
  type        = string
}

variable "runtime_secret_arn" {
  description = "Secrets Manager runtime secret ARN produced by terraform/persistent"
  type        = string
}

variable "artifact_prefix" {
  description = "S3 prefix reserved for this environment"
  type        = string
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
  description = "Password for the ubuntu user (xrdp login). Leave empty to skip cloud-init password setup."
  type        = string
  sensitive   = true
  default     = ""
}
