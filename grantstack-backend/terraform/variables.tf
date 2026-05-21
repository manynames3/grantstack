variable "aws_region" {
  description = "AWS region for GrantStack resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short service name used for resource naming."
  type        = string
  default     = "grantstack"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.project_name))
    error_message = "project_name must be 2-31 characters, start with a lowercase letter, and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.environment))
    error_message = "environment must be 2-21 characters, start with a lowercase letter, and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "lambda_runtime" {
  description = "Python runtime for both Lambda functions."
  type        = string
  default     = "python3.12"
}

variable "ingestion_timeout_seconds" {
  description = "Short timeout for the ingestion Lambda. Keep this low to minimize cost and client wait time."
  type        = number
  default     = 10

  validation {
    condition     = var.ingestion_timeout_seconds >= 3 && var.ingestion_timeout_seconds <= 30
    error_message = "ingestion_timeout_seconds must be between 3 and 30."
  }
}

variable "processor_memory_size" {
  description = "Memory for the processor Lambda. Use 512-1024 MB for heavier orchestration work."
  type        = number
  default     = 1024

  validation {
    condition     = contains([512, 768, 1024], var.processor_memory_size)
    error_message = "processor_memory_size must be one of 512, 768, or 1024."
  }
}

variable "processor_timeout_seconds" {
  description = "Processor Lambda timeout. AWS Lambda maximum is 900 seconds."
  type        = number
  default     = 900

  validation {
    condition     = var.processor_timeout_seconds >= 30 && var.processor_timeout_seconds <= 900
    error_message = "processor_timeout_seconds must be between 30 and 900."
  }
}

variable "sqs_visibility_timeout_seconds" {
  description = "SQS visibility timeout. Must be greater than the processor Lambda timeout."
  type        = number
  default     = 960
}

variable "sqs_message_retention_seconds" {
  description = "Processing queue message retention in seconds."
  type        = number
  default     = 345600

  validation {
    condition     = var.sqs_message_retention_seconds >= 60 && var.sqs_message_retention_seconds <= 1209600
    error_message = "sqs_message_retention_seconds must be between 60 and 1209600."
  }
}

variable "dlq_max_receive_count" {
  description = "Number of failed receives before moving a message to the DLQ."
  type        = number
  default     = 3

  validation {
    condition     = var.dlq_max_receive_count >= 1 && var.dlq_max_receive_count <= 10
    error_message = "dlq_max_receive_count must be between 1 and 10."
  }
}

variable "dynamodb_table_name" {
  description = "Optional DynamoDB table name override. Leave null for generated naming."
  type        = string
  default     = null
}

variable "project_ttl_days" {
  description = "Number of days to retain project records before DynamoDB TTL can expire them. Set to 0 to disable app-level expiration values."
  type        = number
  default     = 30

  validation {
    condition     = var.project_ttl_days >= 0 && var.project_ttl_days <= 365
    error_message = "project_ttl_days must be between 0 and 365."
  }
}

variable "log_retention_in_days" {
  description = "CloudWatch log retention for Lambda log groups."
  type        = number
  default     = 14
}

variable "allowed_origins" {
  description = "CORS allowed origins for the HTTP API. Restrict this in production."
  type        = list(string)
  default     = ["*"]
}

variable "jwt_authorizer" {
  description = "Optional JWT authorizer for the HTTP API. Enable in production with an OIDC/Cognito/Auth0-compatible issuer."
  type = object({
    enabled  = bool
    issuer   = string
    audience = list(string)
  })
  default = {
    enabled  = false
    issuer   = ""
    audience = []
  }

  validation {
    condition = (
      !var.jwt_authorizer.enabled ||
      (
        length(trimspace(var.jwt_authorizer.issuer)) > 0 &&
        length(var.jwt_authorizer.audience) > 0
      )
    )
    error_message = "When jwt_authorizer.enabled is true, issuer and at least one audience value are required."
  }
}

variable "api_throttle_burst_limit" {
  description = "Burst throttle for the HTTP API default stage. Keeps accidental or abusive traffic from creating unexpected cost."
  type        = number
  default     = 20

  validation {
    condition     = var.api_throttle_burst_limit >= 1 && var.api_throttle_burst_limit <= 5000
    error_message = "api_throttle_burst_limit must be between 1 and 5000."
  }
}

variable "api_throttle_rate_limit" {
  description = "Steady-state requests per second for the HTTP API default stage."
  type        = number
  default     = 10

  validation {
    condition     = var.api_throttle_rate_limit >= 1 && var.api_throttle_rate_limit <= 10000
    error_message = "api_throttle_rate_limit must be between 1 and 10000."
  }
}

variable "vector_db_endpoint" {
  description = "Serverless vector database query endpoint, such as a Pinecone Serverless index host. Leave blank while using mocked external calls."
  type        = string
  default     = ""
}

variable "vector_db_provider" {
  description = "Vector retrieval provider. Use pinecone for Pinecone Serverless or generic_json for a custom JSON query endpoint."
  type        = string
  default     = "pinecone"

  validation {
    condition     = contains(["pinecone", "generic_json"], var.vector_db_provider)
    error_message = "vector_db_provider must be pinecone or generic_json."
  }
}

variable "vector_db_api_key_secret_arn" {
  description = "Secrets Manager ARN containing the vector database API key. Required when mock_external_calls is false and no direct env override is used."
  type        = string
  default     = null
}

variable "embedding_provider" {
  description = "Embedding provider used before vector retrieval."
  type        = string
  default     = "openai"

  validation {
    condition     = contains(["openai"], var.embedding_provider)
    error_message = "embedding_provider must be openai."
  }
}

variable "embedding_api_endpoint" {
  description = "Embedding API endpoint. For OpenAI, use https://api.openai.com/v1/embeddings."
  type        = string
  default     = "https://api.openai.com/v1/embeddings"
}

variable "embedding_api_key_secret_arn" {
  description = "Secrets Manager ARN containing the embedding provider API key. Often the same secret as the LLM key."
  type        = string
  default     = null
}

variable "embedding_model" {
  description = "Embedding model identifier."
  type        = string
  default     = "text-embedding-3-small"
}

variable "llm_provider" {
  description = "LLM provider used when mock_external_calls is false."
  type        = string
  default     = "openai"

  validation {
    condition     = contains(["openai", "anthropic", "generic_json"], var.llm_provider)
    error_message = "llm_provider must be openai, anthropic, or generic_json."
  }
}

variable "llm_api_endpoint" {
  description = "External LLM API endpoint. Leave blank while using mocked external calls."
  type        = string
  default     = "https://api.openai.com/v1/chat/completions"
}

variable "llm_api_key_secret_arn" {
  description = "Secrets Manager ARN containing the external LLM API key. Required when mock_external_calls is false and no direct env override is used."
  type        = string
  default     = null
}

variable "llm_model" {
  description = "LLM model identifier passed to the external provider."
  type        = string
  default     = "grantstack-evidence-engine-v1"
}

variable "mock_external_calls" {
  description = "When true, the processor returns deterministic mocked vector and LLM responses."
  type        = bool
  default     = true
}

variable "http_client_timeout_seconds" {
  description = "Timeout for outbound vector DB and LLM HTTP calls."
  type        = number
  default     = 20

  validation {
    condition     = var.http_client_timeout_seconds >= 3 && var.http_client_timeout_seconds <= 120
    error_message = "http_client_timeout_seconds must be between 3 and 120."
  }
}

variable "enable_xray_tracing" {
  description = "Enable active AWS X-Ray tracing on Lambda functions."
  type        = bool
  default     = true
}

variable "enable_api_access_logs" {
  description = "Enable structured HTTP API access logs in CloudWatch Logs."
  type        = bool
  default     = true
}

variable "enable_cloudwatch_dashboard" {
  description = "Create a GrantStack CloudWatch dashboard for API, Lambda, SQS, DynamoDB, and source-refresh health."
  type        = bool
  default     = true
}

variable "api_access_log_retention_in_days" {
  description = "CloudWatch retention for API Gateway access logs."
  type        = number
  default     = 14
}

variable "enable_source_refresh" {
  description = "Create the scheduled source-refresh Lambda and S3 source catalog."
  type        = bool
  default     = true
}

variable "source_refresh_schedule_expression" {
  description = "EventBridge schedule expression for source catalog refresh."
  type        = string
  default     = "rate(7 days)"
}

variable "source_catalog_bucket_name" {
  description = "Optional S3 bucket override for the refreshed source catalog."
  type        = string
  default     = null
}

variable "source_catalog_key" {
  description = "S3 key for the active source catalog JSON."
  type        = string
  default     = "catalog/incentive_catalog.json"
}

variable "enable_cloudwatch_alarms" {
  description = "Create production CloudWatch alarms for Lambda, SQS, and DLQ health."
  type        = bool
  default     = true
}

variable "alarm_actions" {
  description = "Optional SNS topic ARNs or other CloudWatch alarm action ARNs."
  type        = list(string)
  default     = []
}

variable "queue_oldest_message_alarm_seconds" {
  description = "Alarm threshold for the oldest visible processing queue message."
  type        = number
  default     = 300

  validation {
    condition     = var.queue_oldest_message_alarm_seconds >= 60
    error_message = "queue_oldest_message_alarm_seconds must be at least 60."
  }
}

variable "tags" {
  description = "Additional tags applied to AWS resources."
  type        = map(string)
  default     = {}
}
