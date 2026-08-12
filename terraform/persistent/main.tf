terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Use a distinct key such as persistent.tfstate. Never destroy this stack as
  # part of the daily worker lifecycle.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}

resource "aws_ebs_volume" "jobbots" {
  availability_zone = var.availability_zone
  encrypted         = true
  size              = var.volume_size_gb
  type              = "gp3"
  iops              = var.volume_iops
  throughput        = var.volume_throughput

  lifecycle {
    precondition {
      condition     = var.deployment_tier != "canary" || strcontains(var.resource_prefix, "canary")
      error_message = "Canary persistent resources require a resource_prefix containing 'canary'."
    }
  }

  tags = {
    Name           = "${var.resource_prefix}-persistent"
    Environment    = var.environment
    DeploymentTier = var.deployment_tier
    ResourcePrefix = var.resource_prefix
    Project        = "jobbots"
    Persistent     = "true"
    ManagedBy      = "terraform"
  }
}

resource "aws_dynamodb_table" "profile_leases" {
  name         = "${var.resource_prefix}-profile-leases"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "profile_id"

  attribute {
    name = "profile_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Environment    = var.environment
    DeploymentTier = var.deployment_tier
    ResourcePrefix = var.resource_prefix
    Project        = "jobbots"
    Persistent     = "true"
    ManagedBy      = "terraform"
  }
}

resource "aws_secretsmanager_secret" "runtime" {
  name                    = "${var.resource_prefix}/runtime"
  recovery_window_in_days = 30

  tags = {
    Environment    = var.environment
    DeploymentTier = var.deployment_tier
    ResourcePrefix = var.resource_prefix
    Project        = "jobbots"
    Persistent     = "true"
    ManagedBy      = "terraform"
  }
}

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${var.resource_prefix}-artifacts-"

  tags = {
    Environment    = var.environment
    DeploymentTier = var.deployment_tier
    ResourcePrefix = var.resource_prefix
    Project        = "jobbots"
    Persistent     = "true"
    ManagedBy      = "terraform"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-old-artifacts"
    status = "Enabled"

    filter {}

    transition {
      days          = var.artifact_transition_days
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = var.artifact_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.artifact_noncurrent_retention_days
    }
  }
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_function.py"
  output_path = "${path.module}/lambda_function.zip"
}

resource "aws_iam_role" "lambda_exec" {
  name_prefix = "${var.resource_prefix}-lambda-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_secrets_s3" {
  name = "lambda-secrets-s3"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.runtime.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.artifacts.arn}/*"
      }
    ]
  })
}

resource "aws_lambda_function" "completion_trigger" {
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  function_name    = "${var.resource_prefix}-completion-trigger"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30

  environment {
    variables = {
      RESOURCE_PREFIX = var.resource_prefix
    }
  }
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowExecutionFromS3Bucket"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.completion_trigger.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.artifacts.arn
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = aws_s3_bucket.artifacts.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.completion_trigger.arn
    events              = ["s3:ObjectCreated:*"]
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.allow_s3]
}

