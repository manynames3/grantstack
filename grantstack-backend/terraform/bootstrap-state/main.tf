terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  bucket_name = coalesce(
    var.state_bucket_name,
    "${var.project_name}-tf-state-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  )

  common_tags = merge(
    var.tags,
    {
      Application = "GrantStack"
      ManagedBy   = "Terraform"
      Purpose     = "RemoteState"
    }
  )
}

resource "aws_s3_bucket" "state" {
  bucket = local.bucket_name

  tags = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "retain-noncurrent-state"
    status = "Enabled"

    filter {
      prefix = ""
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_state_retention_days
    }
  }
}

output "state_bucket_name" {
  description = "S3 bucket to use in backend/*.hcl files."
  value       = aws_s3_bucket.state.bucket
}

output "state_bucket_region" {
  description = "AWS region for the S3 state bucket."
  value       = var.aws_region
}
