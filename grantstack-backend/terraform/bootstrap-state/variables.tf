variable "aws_region" {
  description = "AWS region for the Terraform state bucket."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project prefix for state resources."
  type        = string
  default     = "grantstack"
}

variable "state_bucket_name" {
  description = "Optional globally unique S3 bucket name. Leave null for account-derived naming."
  type        = string
  default     = null
}

variable "noncurrent_state_retention_days" {
  description = "Days to retain noncurrent Terraform state object versions."
  type        = number
  default     = 90
}

variable "tags" {
  description = "Additional resource tags."
  type        = map(string)
  default     = {}
}
