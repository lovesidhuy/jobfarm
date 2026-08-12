packer {
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = ">= 1.0.0"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "image_name" {
  type    = string
  default = ""
}

variable "environment" {
  type = string
}

variable "deployment_tier" {
  type = string
}

variable "resource_prefix" {
  type = string
}

locals {
  timestamp  = formatdate("YYYYMMDDhhmm", timestamp())
  image_name = var.image_name != "" ? var.image_name : "jobbots-linux-golden-${local.timestamp}"
}

source "amazon-ebs" "golden" {
  region        = var.aws_region
  instance_type = "m6i.large"
  ssh_username  = "ubuntu"

  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = ["099720109477"]
  }

  ami_name = local.image_name

  run_tags = {
    Environment    = var.environment
    DeploymentTier = var.deployment_tier
    ResourcePrefix = var.resource_prefix
    Project        = "jobbots"
    Ephemeral      = "true"
  }

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 64
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Environment    = var.environment
    DeploymentTier = var.deployment_tier
    ResourcePrefix = var.resource_prefix
    Project        = "jobbots"
    Platform       = "linux"
    BuiltBy        = "packer"
    BuiltAt        = local.timestamp
  }
}

build {
  sources = ["source.amazon-ebs.golden"]

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
      "sudo test -f /etc/systemd/system/jobbots-nstbrowser.service",
      "/opt/jobbots/venv/bin/python -c 'import playwright, selenium, pymongo; print(\"python deps ok\")'",
      "sudo docker image inspect mongo:8.0 >/dev/null",
      "sudo docker image inspect nstbrowser/browserless:latest >/dev/null"
    ]
  }
}
