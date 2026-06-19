mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}
mock_provider "archive" {}

run "dev_worker_is_disabled_by_default" {
  command = plan

  variables {
    environment = "dev"
  }

  assert {
    condition     = aws_lambda_event_source_mapping.processor_sqs.enabled == false
    error_message = "The dev SQS worker must not poll unless it is explicitly enabled."
  }

  assert {
    condition     = aws_sqs_queue.processing.receive_wait_time_seconds == 20
    error_message = "The processing queue must use 20-second long polling."
  }

  assert {
    condition     = aws_sqs_queue.processing_dlq.receive_wait_time_seconds == 20
    error_message = "The processing DLQ must use 20-second long polling."
  }
}

run "dev_worker_can_be_enabled_explicitly" {
  command = plan

  variables {
    environment       = "dev"
    enable_sqs_worker = true
  }

  assert {
    condition     = aws_lambda_event_source_mapping.processor_sqs.enabled == true
    error_message = "The dev SQS worker should run when explicitly enabled."
  }
}

run "production_worker_remains_enabled_by_default" {
  command = plan

  variables {
    environment = "prod"
  }

  assert {
    condition     = aws_lambda_event_source_mapping.processor_sqs.enabled == true
    error_message = "The production SQS worker must remain enabled by default."
  }
}
