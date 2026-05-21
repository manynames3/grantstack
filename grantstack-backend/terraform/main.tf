terraform {
  required_version = ">= 1.6.0"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  name_prefix                = "${var.project_name}-${var.environment}"
  dynamodb_table_name        = coalesce(var.dynamodb_table_name, "${local.name_prefix}-projects")
  source_catalog_bucket_name = coalesce(var.source_catalog_bucket_name, "${local.name_prefix}-source-catalog-${data.aws_caller_identity.current.account_id}")
  external_secret_arns = compact([
    var.vector_db_api_key_secret_arn,
    var.embedding_api_key_secret_arn,
    var.llm_api_key_secret_arn
  ])

  common_tags = merge(
    var.tags,
    {
      Application = "GrantStack"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  )
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

data "archive_file" "ingestion_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/ingest_handler.py"
  output_path = "${path.module}/ingest_handler.zip"
}

data "archive_file" "processor_lambda" {
  type        = "zip"
  output_path = "${path.module}/processor_handler.zip"

  source {
    content  = file("${path.module}/../lambda/processor_handler.py")
    filename = "processor_handler.py"
  }

  source {
    content  = file("${path.module}/../lambda/incentive_catalog.json")
    filename = "incentive_catalog.json"
  }
}

data "archive_file" "report_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/report_handler.py"
  output_path = "${path.module}/report_handler.zip"
}

data "archive_file" "source_refresh_lambda" {
  type        = "zip"
  output_path = "${path.module}/source_refresh_handler.zip"

  source {
    content  = file("${path.module}/../lambda/source_refresh_handler.py")
    filename = "source_refresh_handler.py"
  }

  source {
    content  = file("${path.module}/../lambda/incentive_catalog.json")
    filename = "incentive_catalog.json"
  }
}

resource "aws_sqs_queue" "processing_dlq" {
  name                      = "${local.name_prefix}-processing-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = local.common_tags
}

resource "aws_sqs_queue" "processing" {
  name                       = "${local.name_prefix}-processing"
  visibility_timeout_seconds = var.sqs_visibility_timeout_seconds
  message_retention_seconds  = var.sqs_message_retention_seconds
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.processing_dlq.arn
    maxReceiveCount     = var.dlq_max_receive_count
  })

  lifecycle {
    precondition {
      condition     = var.sqs_visibility_timeout_seconds > var.processor_timeout_seconds
      error_message = "sqs_visibility_timeout_seconds must be greater than processor_timeout_seconds."
    }
  }

  tags = local.common_tags
}

resource "aws_dynamodb_table" "projects" {
  name         = local.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "project_id"

  attribute {
    name = "project_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = local.common_tags
}

resource "aws_s3_bucket" "source_catalog" {
  count = var.enable_source_refresh ? 1 : 0

  bucket = local.source_catalog_bucket_name

  tags = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "source_catalog" {
  count = var.enable_source_refresh ? 1 : 0

  bucket = aws_s3_bucket.source_catalog[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "source_catalog" {
  count = var.enable_source_refresh ? 1 : 0

  bucket = aws_s3_bucket.source_catalog[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "source_catalog" {
  count = var.enable_source_refresh ? 1 : 0

  bucket = aws_s3_bucket.source_catalog[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "source_catalog" {
  count = var.enable_source_refresh ? 1 : 0

  bucket = aws_s3_bucket.source_catalog[0].id

  rule {
    id     = "expire-old-catalog-versions"
    status = "Enabled"

    filter {
      prefix = "catalog/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_object" "source_catalog_seed" {
  count = var.enable_source_refresh ? 1 : 0

  bucket                 = aws_s3_bucket.source_catalog[0].id
  key                    = var.source_catalog_key
  source                 = "${path.module}/../lambda/incentive_catalog.json"
  source_hash            = filemd5("${path.module}/../lambda/incentive_catalog.json")
  content_type           = "application/json"
  server_side_encryption = "AES256"

  lifecycle {
    ignore_changes = all
  }
}

resource "aws_cloudwatch_log_group" "ingestion" {
  name              = "/aws/lambda/${local.name_prefix}-ingest"
  retention_in_days = var.log_retention_in_days

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${local.name_prefix}-processor"
  retention_in_days = var.log_retention_in_days

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "report" {
  name              = "/aws/lambda/${local.name_prefix}-report"
  retention_in_days = var.log_retention_in_days

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "source_refresh" {
  count = var.enable_source_refresh ? 1 : 0

  name              = "/aws/lambda/${local.name_prefix}-source-refresh"
  retention_in_days = var.log_retention_in_days

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "api_access" {
  count = var.enable_api_access_logs ? 1 : 0

  name              = "/aws/apigateway/${local.name_prefix}-http-api"
  retention_in_days = var.api_access_log_retention_in_days

  tags = local.common_tags
}

resource "aws_iam_role" "ingestion" {
  name               = "${local.name_prefix}-ingest-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

data "aws_iam_policy_document" "ingestion" {
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      aws_cloudwatch_log_group.ingestion.arn,
      "${aws_cloudwatch_log_group.ingestion.arn}:*"
    ]
  }

  statement {
    sid     = "SendProcessingMessages"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]

    resources = [aws_sqs_queue.processing.arn]
  }

  statement {
    sid    = "CreateAcceptedProjects"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem"
    ]

    resources = [aws_dynamodb_table.projects.arn]
  }

  dynamic "statement" {
    for_each = var.enable_xray_tracing ? [1] : []

    content {
      sid    = "WriteXRayTraces"
      effect = "Allow"
      actions = [
        "xray:PutTelemetryRecords",
        "xray:PutTraceSegments"
      ]
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "ingestion" {
  name   = "${local.name_prefix}-ingest-policy"
  role   = aws_iam_role.ingestion.id
  policy = data.aws_iam_policy_document.ingestion.json
}

resource "aws_iam_role" "processor" {
  name               = "${local.name_prefix}-processor-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

data "aws_iam_policy_document" "processor" {
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      aws_cloudwatch_log_group.processor.arn,
      "${aws_cloudwatch_log_group.processor.arn}:*"
    ]
  }

  statement {
    sid    = "ConsumeProcessingQueue"
    effect = "Allow"

    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility"
    ]

    resources = [aws_sqs_queue.processing.arn]
  }

  statement {
    sid    = "WriteProjectReports"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem"
    ]

    resources = [aws_dynamodb_table.projects.arn]
  }

  dynamic "statement" {
    for_each = length(local.external_secret_arns) > 0 ? [1] : []

    content {
      sid    = "ReadConfiguredExternalSecrets"
      effect = "Allow"

      actions = ["secretsmanager:GetSecretValue"]

      resources = local.external_secret_arns
    }
  }

  dynamic "statement" {
    for_each = var.enable_source_refresh ? [1] : []

    content {
      sid     = "ReadSourceCatalog"
      effect  = "Allow"
      actions = ["s3:GetObject"]

      resources = ["${aws_s3_bucket.source_catalog[0].arn}/${var.source_catalog_key}"]
    }
  }

  dynamic "statement" {
    for_each = var.enable_xray_tracing ? [1] : []

    content {
      sid    = "WriteXRayTraces"
      effect = "Allow"
      actions = [
        "xray:PutTelemetryRecords",
        "xray:PutTraceSegments"
      ]
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "processor" {
  name   = "${local.name_prefix}-processor-policy"
  role   = aws_iam_role.processor.id
  policy = data.aws_iam_policy_document.processor.json
}

resource "aws_iam_role" "report" {
  name               = "${local.name_prefix}-report-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

data "aws_iam_policy_document" "report" {
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      aws_cloudwatch_log_group.report.arn,
      "${aws_cloudwatch_log_group.report.arn}:*"
    ]
  }

  statement {
    sid     = "ReadProjectReports"
    effect  = "Allow"
    actions = ["dynamodb:GetItem"]

    resources = [aws_dynamodb_table.projects.arn]
  }

  dynamic "statement" {
    for_each = var.enable_xray_tracing ? [1] : []

    content {
      sid    = "WriteXRayTraces"
      effect = "Allow"
      actions = [
        "xray:PutTelemetryRecords",
        "xray:PutTraceSegments"
      ]
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "report" {
  name   = "${local.name_prefix}-report-policy"
  role   = aws_iam_role.report.id
  policy = data.aws_iam_policy_document.report.json
}

resource "aws_iam_role" "source_refresh" {
  count = var.enable_source_refresh ? 1 : 0

  name               = "${local.name_prefix}-source-refresh-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

data "aws_iam_policy_document" "source_refresh" {
  count = var.enable_source_refresh ? 1 : 0

  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      aws_cloudwatch_log_group.source_refresh[0].arn,
      "${aws_cloudwatch_log_group.source_refresh[0].arn}:*"
    ]
  }

  statement {
    sid    = "ReadWriteSourceCatalog"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject"
    ]

    resources = ["${aws_s3_bucket.source_catalog[0].arn}/${var.source_catalog_key}"]
  }

  dynamic "statement" {
    for_each = var.enable_xray_tracing ? [1] : []

    content {
      sid    = "WriteXRayTraces"
      effect = "Allow"
      actions = [
        "xray:PutTelemetryRecords",
        "xray:PutTraceSegments"
      ]
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "source_refresh" {
  count = var.enable_source_refresh ? 1 : 0

  name   = "${local.name_prefix}-source-refresh-policy"
  role   = aws_iam_role.source_refresh[0].id
  policy = data.aws_iam_policy_document.source_refresh[0].json
}

resource "aws_lambda_function" "ingestion" {
  function_name = "${local.name_prefix}-ingest"
  description   = "Validates GrantStack project specs and queues asynchronous processing work."
  role          = aws_iam_role.ingestion.arn
  runtime       = var.lambda_runtime
  handler       = "ingest_handler.lambda_handler"
  architectures = ["arm64"]

  filename         = data.archive_file.ingestion_lambda.output_path
  source_code_hash = data.archive_file.ingestion_lambda.output_base64sha256

  memory_size = 128
  timeout     = var.ingestion_timeout_seconds

  tracing_config {
    mode = var.enable_xray_tracing ? "Active" : "PassThrough"
  }

  environment {
    variables = {
      SQS_QUEUE_URL       = aws_sqs_queue.processing.url
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.projects.name
      PROJECT_TTL_DAYS    = tostring(var.project_ttl_days)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.ingestion,
    aws_iam_role_policy.ingestion
  ]

  tags = local.common_tags
}

resource "aws_lambda_function" "processor" {
  function_name = "${local.name_prefix}-processor"
  description   = "Processes GrantStack project specs, orchestrates vector context and LLM analysis, and writes reports."
  role          = aws_iam_role.processor.arn
  runtime       = var.lambda_runtime
  handler       = "processor_handler.lambda_handler"
  architectures = ["arm64"]

  filename         = data.archive_file.processor_lambda.output_path
  source_code_hash = data.archive_file.processor_lambda.output_base64sha256

  memory_size = var.processor_memory_size
  timeout     = var.processor_timeout_seconds

  tracing_config {
    mode = var.enable_xray_tracing ? "Active" : "PassThrough"
  }

  environment {
    variables = {
      DYNAMODB_TABLE_NAME          = aws_dynamodb_table.projects.name
      SOURCE_CATALOG_BUCKET        = var.enable_source_refresh ? aws_s3_bucket.source_catalog[0].bucket : ""
      SOURCE_CATALOG_KEY           = var.enable_source_refresh ? var.source_catalog_key : ""
      VECTOR_DB_PROVIDER           = var.vector_db_provider
      VECTOR_DB_ENDPOINT           = trimspace(var.vector_db_endpoint) == "" ? "__MOCK_DISABLED__" : var.vector_db_endpoint
      VECTOR_DB_API_KEY_SECRET_ARN = var.vector_db_api_key_secret_arn == null ? "__MOCK_DISABLED__" : var.vector_db_api_key_secret_arn
      EMBEDDING_PROVIDER           = var.embedding_provider
      EMBEDDING_API_ENDPOINT       = trimspace(var.embedding_api_endpoint) == "" ? "__MOCK_DISABLED__" : var.embedding_api_endpoint
      EMBEDDING_API_KEY_SECRET_ARN = var.embedding_api_key_secret_arn == null ? "__MOCK_DISABLED__" : var.embedding_api_key_secret_arn
      EMBEDDING_MODEL              = var.embedding_model
      LLM_PROVIDER                 = var.llm_provider
      LLM_API_ENDPOINT             = trimspace(var.llm_api_endpoint) == "" ? "__MOCK_DISABLED__" : var.llm_api_endpoint
      LLM_API_KEY_SECRET_ARN       = var.llm_api_key_secret_arn == null ? "__MOCK_DISABLED__" : var.llm_api_key_secret_arn
      LLM_MODEL                    = var.llm_model
      MOCK_EXTERNAL_CALLS          = tostring(var.mock_external_calls)
      HTTP_CLIENT_TIMEOUT_SECS     = tostring(var.http_client_timeout_seconds)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.processor,
    aws_iam_role_policy.processor
  ]

  tags = local.common_tags
}

resource "aws_lambda_function" "report" {
  function_name = "${local.name_prefix}-report"
  description   = "Returns GrantStack project status and reports for token-authorized result links."
  role          = aws_iam_role.report.arn
  runtime       = var.lambda_runtime
  handler       = "report_handler.lambda_handler"
  architectures = ["arm64"]

  filename         = data.archive_file.report_lambda.output_path
  source_code_hash = data.archive_file.report_lambda.output_base64sha256

  memory_size = 128
  timeout     = 10

  tracing_config {
    mode = var.enable_xray_tracing ? "Active" : "PassThrough"
  }

  environment {
    variables = {
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.projects.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.report,
    aws_iam_role_policy.report
  ]

  tags = local.common_tags
}

resource "aws_lambda_function" "source_refresh" {
  count = var.enable_source_refresh ? 1 : 0

  function_name = "${local.name_prefix}-source-refresh"
  description   = "Refreshes GrantStack source catalog metadata from official source URLs."
  role          = aws_iam_role.source_refresh[0].arn
  runtime       = var.lambda_runtime
  handler       = "source_refresh_handler.lambda_handler"
  architectures = ["arm64"]

  filename         = data.archive_file.source_refresh_lambda.output_path
  source_code_hash = data.archive_file.source_refresh_lambda.output_base64sha256

  memory_size = 256
  timeout     = 120

  tracing_config {
    mode = var.enable_xray_tracing ? "Active" : "PassThrough"
  }

  environment {
    variables = {
      SOURCE_CATALOG_BUCKET = aws_s3_bucket.source_catalog[0].bucket
      SOURCE_CATALOG_KEY    = var.source_catalog_key
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.source_refresh,
    aws_iam_role_policy.source_refresh,
    aws_s3_object.source_catalog_seed
  ]

  tags = local.common_tags
}

resource "aws_lambda_event_source_mapping" "processor_sqs" {
  event_source_arn                   = aws_sqs_queue.processing.arn
  function_name                      = aws_lambda_function.processor.arn
  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
  function_response_types            = ["ReportBatchItemFailures"]
  enabled                            = true
}

resource "aws_cloudwatch_event_rule" "source_refresh" {
  count = var.enable_source_refresh ? 1 : 0

  name                = "${local.name_prefix}-source-refresh"
  description         = "Refresh GrantStack official source catalog metadata."
  schedule_expression = var.source_refresh_schedule_expression

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "source_refresh" {
  count = var.enable_source_refresh ? 1 : 0

  rule      = aws_cloudwatch_event_rule.source_refresh[0].name
  target_id = "${local.name_prefix}-source-refresh"
  arn       = aws_lambda_function.source_refresh[0].arn
}

resource "aws_lambda_permission" "allow_eventbridge_source_refresh" {
  count = var.enable_source_refresh ? 1 : 0

  statement_id  = "AllowSourceRefreshFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.source_refresh[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.source_refresh[0].arn
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_headers = ["content-type", "authorization"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_origins = var.allowed_origins
    max_age       = 300
  }

  tags = local.common_tags
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  count = var.jwt_authorizer.enabled ? 1 : 0

  api_id           = aws_apigatewayv2_api.http.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${local.name_prefix}-jwt"

  jwt_configuration {
    audience = var.jwt_authorizer.audience
    issuer   = var.jwt_authorizer.issuer
  }
}

resource "aws_apigatewayv2_integration" "ingestion" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingestion.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_integration" "report" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.report.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "post_projects" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "POST /projects"
  authorization_type = var.jwt_authorizer.enabled ? "JWT" : "NONE"
  authorizer_id      = var.jwt_authorizer.enabled ? aws_apigatewayv2_authorizer.jwt[0].id : null
  target             = "integrations/${aws_apigatewayv2_integration.ingestion.id}"
}

resource "aws_apigatewayv2_route" "get_project_report" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /projects/{project_id}"
  authorization_type = "NONE"
  target             = "integrations/${aws_apigatewayv2_integration.report.id}"
}

resource "aws_apigatewayv2_route" "get_projects_index" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /projects"
  authorization_type = "NONE"
  target             = "integrations/${aws_apigatewayv2_integration.report.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    detailed_metrics_enabled = true
    throttling_burst_limit   = var.api_throttle_burst_limit
    throttling_rate_limit    = var.api_throttle_rate_limit
  }

  dynamic "access_log_settings" {
    for_each = var.enable_api_access_logs ? [1] : []

    content {
      destination_arn = aws_cloudwatch_log_group.api_access[0].arn
      format = jsonencode({
        requestId               = "$context.requestId"
        extendedRequestId       = "$context.extendedRequestId"
        ip                      = "$context.identity.sourceIp"
        requestTime             = "$context.requestTime"
        httpMethod              = "$context.httpMethod"
        routeKey                = "$context.routeKey"
        status                  = "$context.status"
        protocol                = "$context.protocol"
        responseLength          = "$context.responseLength"
        responseLatency         = "$context.responseLatency"
        integrationErrorMessage = "$context.integrationErrorMessage"
      })
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_permission" "allow_http_api" {
  statement_id  = "AllowExecutionFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_lambda_permission" "allow_http_api_report" {
  statement_id  = "AllowReportExecutionFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.report.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_cloudwatch_metric_alarm" "ingestion_errors" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-ingestion-errors"
  alarm_description   = "Ingestion Lambda has one or more errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions

  dimensions = {
    FunctionName = aws_lambda_function.ingestion.function_name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "processor_errors" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-processor-errors"
  alarm_description   = "Processor Lambda has one or more errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions

  dimensions = {
    FunctionName = aws_lambda_function.processor.function_name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "report_errors" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-report-errors"
  alarm_description   = "Report Lambda has one or more errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions

  dimensions = {
    FunctionName = aws_lambda_function.report.function_name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "source_refresh_errors" {
  count = var.enable_cloudwatch_alarms && var.enable_source_refresh ? 1 : 0

  alarm_name          = "${local.name_prefix}-source-refresh-errors"
  alarm_description   = "Source refresh Lambda has one or more errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions

  dimensions = {
    FunctionName = aws_lambda_function.source_refresh[0].function_name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_log_metric_filter" "processor_record_failures" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  name           = "${local.name_prefix}-processor-record-failures"
  log_group_name = aws_cloudwatch_log_group.processor.name
  pattern        = "\"project_processing_failed\""

  metric_transformation {
    name      = "${local.name_prefix}-processor-record-failures"
    namespace = "GrantStack"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "processor_record_failures" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-processor-record-failures"
  alarm_description   = "Processor reported one or more SQS record-level processing failures."
  namespace           = "GrantStack"
  metric_name         = "${local.name_prefix}-processor-record-failures"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions

  depends_on = [aws_cloudwatch_log_metric_filter.processor_record_failures]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "processing_queue_age" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-processing-queue-age"
  alarm_description   = "Oldest visible processing queue message has exceeded the expected processing delay."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  threshold           = var.queue_oldest_message_alarm_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions

  dimensions = {
    QueueName = aws_sqs_queue.processing.name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "dlq_visible_messages" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-dlq-visible-messages"
  alarm_description   = "One or more GrantStack messages are parked in the DLQ."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions

  dimensions = {
    QueueName = aws_sqs_queue.processing_dlq.name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_dashboard" "grantstack" {
  count = var.enable_cloudwatch_dashboard ? 1 : 0

  dashboard_name = "${local.name_prefix}-operations"
  dashboard_body = jsonencode({
    widgets = concat(
      [
        {
          type   = "metric"
          x      = 0
          y      = 0
          width  = 12
          height = 6
          properties = {
            region = var.aws_region
            title  = "HTTP API traffic and errors"
            metrics = [
              ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.http.id, { stat = "Sum", label = "Requests" }],
              [".", "4xx", ".", ".", { stat = "Sum", label = "4xx" }],
              [".", "5xx", ".", ".", { stat = "Sum", label = "5xx" }],
              [".", "Latency", ".", ".", { stat = "Average", label = "Avg latency" }]
            ]
            period = 60
            view   = "timeSeries"
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 0
          width  = 12
          height = 6
          properties = {
            region = var.aws_region
            title  = "Lambda errors"
            metrics = [
              ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.ingestion.function_name, { stat = "Sum", label = "Ingestion" }],
              [".", ".", ".", aws_lambda_function.processor.function_name, { stat = "Sum", label = "Processor" }],
              [".", ".", ".", aws_lambda_function.report.function_name, { stat = "Sum", label = "Report" }]
            ]
            period = 60
            view   = "timeSeries"
          }
        },
        {
          type   = "metric"
          x      = 0
          y      = 6
          width  = 12
          height = 6
          properties = {
            region = var.aws_region
            title  = "Lambda duration"
            metrics = [
              ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.ingestion.function_name, { stat = "p95", label = "Ingestion p95" }],
              [".", ".", ".", aws_lambda_function.processor.function_name, { stat = "p95", label = "Processor p95" }],
              [".", ".", ".", aws_lambda_function.report.function_name, { stat = "p95", label = "Report p95" }]
            ]
            period = 60
            view   = "timeSeries"
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 6
          width  = 12
          height = 6
          properties = {
            region = var.aws_region
            title  = "SQS queue health"
            metrics = [
              ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.processing.name, { stat = "Maximum", label = "Processing visible" }],
              [".", "ApproximateAgeOfOldestMessage", ".", aws_sqs_queue.processing.name, { stat = "Maximum", label = "Oldest age" }],
              [".", "ApproximateNumberOfMessagesVisible", ".", aws_sqs_queue.processing_dlq.name, { stat = "Maximum", label = "DLQ visible" }]
            ]
            period = 60
            view   = "timeSeries"
          }
        },
        {
          type   = "metric"
          x      = 0
          y      = 12
          width  = 12
          height = 6
          properties = {
            region = var.aws_region
            title  = "DynamoDB on-demand activity"
            metrics = [
              ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.projects.name, { stat = "Sum", label = "Read capacity units" }],
              [".", "ConsumedWriteCapacityUnits", ".", aws_dynamodb_table.projects.name, { stat = "Sum", label = "Write capacity units" }],
              [".", "ThrottledRequests", ".", aws_dynamodb_table.projects.name, { stat = "Sum", label = "Throttled requests" }]
            ]
            period = 300
            view   = "timeSeries"
          }
        }
      ],
      var.enable_source_refresh ? [
        {
          type   = "metric"
          x      = 12
          y      = 12
          width  = 12
          height = 6
          properties = {
            region = var.aws_region
            title  = "Source refresh"
            metrics = [
              ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.source_refresh[0].function_name, { stat = "Sum", label = "Invocations" }],
              [".", "Errors", ".", aws_lambda_function.source_refresh[0].function_name, { stat = "Sum", label = "Errors" }],
              [".", "Duration", ".", aws_lambda_function.source_refresh[0].function_name, { stat = "p95", label = "Duration p95" }]
            ]
            period = 300
            view   = "timeSeries"
          }
        }
      ] : []
    )
  })
}

output "api_endpoint" {
  description = "HTTP API endpoint for GrantStack ingestion."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "aws_region" {
  description = "AWS region used by this deployment."
  value       = var.aws_region
}

output "api_authorization_type" {
  description = "Authorization mode configured on POST /projects."
  value       = var.jwt_authorizer.enabled ? "JWT" : "NONE"
}

output "projects_table_name" {
  description = "DynamoDB projects table name."
  value       = aws_dynamodb_table.projects.name
}

output "ingestion_function_name" {
  description = "Ingestion Lambda function name."
  value       = aws_lambda_function.ingestion.function_name
}

output "processor_function_name" {
  description = "Processor Lambda function name."
  value       = aws_lambda_function.processor.function_name
}

output "report_function_name" {
  description = "Report Lambda function name."
  value       = aws_lambda_function.report.function_name
}

output "processing_queue_url" {
  description = "SQS processing queue URL."
  value       = aws_sqs_queue.processing.url
}

output "processing_queue_arn" {
  description = "SQS processing queue ARN."
  value       = aws_sqs_queue.processing.arn
}

output "processing_dlq_url" {
  description = "SQS processing DLQ URL."
  value       = aws_sqs_queue.processing_dlq.url
}

output "processing_dlq_arn" {
  description = "SQS processing DLQ ARN."
  value       = aws_sqs_queue.processing_dlq.arn
}

output "source_catalog_bucket" {
  description = "S3 bucket containing the refreshed source catalog."
  value       = var.enable_source_refresh ? aws_s3_bucket.source_catalog[0].bucket : null
}

output "source_catalog_key" {
  description = "S3 key for the active source catalog."
  value       = var.enable_source_refresh ? var.source_catalog_key : null
}

output "source_refresh_function_name" {
  description = "Source refresh Lambda function name."
  value       = var.enable_source_refresh ? aws_lambda_function.source_refresh[0].function_name : null
}

output "cloudwatch_dashboard_name" {
  description = "CloudWatch operations dashboard name."
  value       = var.enable_cloudwatch_dashboard ? aws_cloudwatch_dashboard.grantstack[0].dashboard_name : null
}
