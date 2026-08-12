packer {
  required_plugins {
    googlecompute = {
      source  = "github.com/hashicorp/googlecompute"
      version = ">= 1.0.0"
    }
  }
}

variable "gcp_project_id" {
  type = string
}

variable "gcp_zone" {
  type    = string
  default = "us-west1-a"
}

variable "image_name" {
  type    = string
  default = ""
}

variable "image_family" {
  type    = string
  default = "jobbots-gcp-golden"
}

variable "environment" {
  type    = string
  default = "canary"
}

variable "deployment_tier" {
  type    = string
  default = "canary"
}

variable "resource_prefix" {
  type    = string
  default = "jobbots-canary"
}

locals {
  timestamp  = formatdate("YYYYMMDDhhmm", timestamp())
  image_name = var.image_name != "" ? var.image_name : "jobbots-gcp-golden-${local.timestamp}"
}

source "googlecompute" "golden" {
  project_id          = var.gcp_project_id
  zone                = var.gcp_zone
  source_image_family = "ubuntu-2404-lts-amd64"
  ssh_username        = "ubuntu"
  machine_type        = "e2-standard-4"
  disk_size           = 64
  image_name          = local.image_name
    image_family        = var.image_family

  labels = {
    environment     = var.environment
    deployment_tier = var.deployment_tier
    resource_prefix = var.resource_prefix
    project         = "jobbots"
    built_by        = "packer"
  }
}

build {
  sources = ["source.googlecompute.golden"]

  provisioner "file" {
    source      = "requirements.txt"
    destination = "/tmp/requirements.txt"
  }

  provisioner "file" {
    source      = "packer/linux/bin"
    destination = "/tmp/bin"
  }

  provisioner "file" {
    source      = "packer/linux/systemd"
    destination = "/tmp/systemd"
  }

  provisioner "shell" {
    script          = "packer/scripts/provision_linux.sh"
    execute_command = "sudo -E bash '{{ .Path }}'"
  }

  provisioner "shell" {
    inline = [
      "sudo systemctl is-enabled docker",
      "sudo systemctl is-enabled xrdp",
      "sudo test -x /opt/jobbots/bin/jobbots-artifact-sync",
      "/opt/jobbots/venv/bin/python -c 'import playwright, selenium, pymongo; print(\"python deps ok\")'"
    ]
  }
}
