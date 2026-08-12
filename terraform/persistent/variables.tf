variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "availability_zone" {
  type    = string
  default = "us-west-2a"
}

variable "environment" {
  type    = string
  default = "canary"
}

variable "deployment_tier" {
  type = string

  validation {
    condition     = contains(["canary", "production"], var.deployment_tier)
    error_message = "deployment_tier must be canary or production."
  }
}

variable "resource_prefix" {
  type = string

  validation {
    condition     = length(var.resource_prefix) >= 8 && can(regex("^[a-z0-9-]+$", var.resource_prefix))
    error_message = "resource_prefix must be at least 8 lowercase alphanumeric/hyphen characters."
  }
}

variable "volume_size_gb" {
  type = number
  # Production + canary default (20 GB). Override via PERSISTENT_VOLUME_SIZE_GB / tfvars.
  # EBS can only grow in place — shrinking requires replace (new volume).
  default = 20
}

variable "volume_iops" {
  type    = number
  default = 3000
}

variable "volume_throughput" {
  type    = number
  default = 125
}

variable "artifact_noncurrent_retention_days" {
  type    = number
  default = 90
}

variable "artifact_transition_days" {
  type    = number
  default = 30
}

variable "artifact_retention_days" {
  type    = number
  default = 365
}
