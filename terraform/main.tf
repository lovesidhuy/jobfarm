terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Backend config is injected via -backend-config flags in CI.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_ebs_volume" "persistent" {
  filter {
    name   = "volume-id"
    values = [var.persistent_volume_id]
  }
}

resource "aws_vpc" "automation" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.tags, { Name = "${var.resource_prefix}-vpc" })
}

resource "aws_subnet" "worker" {
  vpc_id                  = aws_vpc.automation.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = true

  tags = merge(local.tags, { Name = "${var.resource_prefix}-subnet" })
}

resource "aws_internet_gateway" "automation" {
  vpc_id = aws_vpc.automation.id
  tags   = merge(local.tags, { Name = "${var.resource_prefix}-igw" })
}

resource "aws_route_table" "worker" {
  vpc_id = aws_vpc.automation.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.automation.id
  }

  tags = merge(local.tags, { Name = "${var.resource_prefix}-route-table" })
}

resource "aws_route_table_association" "worker" {
  subnet_id      = aws_subnet.worker.id
  route_table_id = aws_route_table.worker.id
}

resource "aws_security_group" "worker" {
  name_prefix = "${var.resource_prefix}-"
  description = "Ephemeral jobbots worker security group (RDP optional via allowed_rdp_ip_*)"
  vpc_id      = aws_vpc.automation.id

  dynamic "ingress" {
    for_each = var.allowed_rdp_ip_v4 != "" ? [var.allowed_rdp_ip_v4] : []
    content {
      description = "RDP from operator IPv4"
      from_port   = 3389
      to_port     = 3389
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
    }
  }

  dynamic "ingress" {
    for_each = var.allowed_rdp_ip_v6 != "" ? [var.allowed_rdp_ip_v6] : []
    content {
      description      = "RDP from operator IPv6"
      from_port        = 3389
      to_port          = 3389
      protocol         = "tcp"
      ipv6_cidr_blocks = [ingress.value]
    }
  }

  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = local.tags
}

resource "aws_iam_role" "worker" {
  name_prefix = "${var.resource_prefix}-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "profile_leases" {
  name = "profile-leases"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:DeleteItem",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem"
      ]
      Resource = var.profile_lease_table_arn
    }]
  })
}

resource "aws_iam_role_policy" "artifacts" {
  name = "artifacts"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = var.artifact_bucket_arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["${var.artifact_prefix}/*"]
          }
        }
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = "${var.artifact_bucket_arn}/${var.artifact_prefix}/*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "runtime_secret" {
  name = "runtime-secret"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.runtime_secret_arn
    }]
  })
}

resource "aws_iam_instance_profile" "worker" {
  name_prefix = "${var.resource_prefix}-"
  role        = aws_iam_role.worker.name
}

resource "aws_instance" "automation_vm" {
  ami                         = var.golden_image_id != "" ? var.golden_image_id : data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.worker.id
  vpc_security_group_ids      = [aws_security_group.worker.id]
  iam_instance_profile        = aws_iam_instance_profile.worker.name
  associate_public_ip_address = true
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    encrypted             = true
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    aws_region           = var.aws_region
    persistent_volume_id = var.persistent_volume_id
    profile_lease_table  = var.profile_lease_table_name
    artifact_bucket      = var.artifact_bucket_name
    artifact_prefix      = var.artifact_prefix
    runtime_secret_name  = var.runtime_secret_name
    deployment_tier      = var.deployment_tier
    resource_prefix      = var.resource_prefix
    vm_admin_password    = var.vm_admin_password
  })

  lifecycle {
    precondition {
      condition     = var.deployment_tier != "canary" || strcontains(var.resource_prefix, "canary")
      error_message = "Canary workers require a resource_prefix containing 'canary'."
    }

    precondition {
      condition     = var.deployment_tier != "canary" || strcontains(var.vm_name, "canary")
      error_message = "Canary workers require a vm_name containing 'canary'."
    }

    precondition {
      condition     = data.aws_ebs_volume.persistent.availability_zone == var.availability_zone
      error_message = "The persistent EBS volume must be in the worker availability zone."
    }
  }

  tags = merge(local.tags, {
    Name      = var.vm_name
    Ephemeral = "true"
  })
}

resource "aws_volume_attachment" "persistent" {
  device_name = "/dev/sdf"
  volume_id   = var.persistent_volume_id
  instance_id = aws_instance.automation_vm.id
}

locals {
  tags = {
    Environment    = var.environment
    DeploymentTier = var.deployment_tier
    ResourcePrefix = var.resource_prefix
    Project        = "jobbots"
    ManagedBy      = "terraform"
  }
}
