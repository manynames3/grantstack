# SQS Worker Runbook

GrantStack consumes `grantstack-<environment>-processing` through an AWS Lambda event source mapping. There is no application-side polling loop. Lambda owns the `ReceiveMessage` calls, long polling, scaling, and idle backoff behavior.

The processing queue uses a 20-second receive wait. The 960-second visibility timeout exceeds the processor Lambda's 900-second timeout. The mapping keeps a batch size of one because a single report can use most of that processing window; increasing the batch would risk messages becoming visible before the batch completes. Lambda deletes successfully processed messages and the handler reports individual failures through `ReportBatchItemFailures`.

## Dev Worker Lifecycle

The event source mapping is disabled by default when `environment = "dev"`. This stops idle SQS receives without deleting the queue, DLQ, or pending messages. Staging and production remain enabled by default.

`enable_sqs_worker` is a deployment-time control because the Lambda service poller exists outside the function process. Automation can pass it as `TF_VAR_enable_sqs_worker`; a checked-in or CLI variable value takes normal Terraform precedence.

Enable dev processing intentionally:

```sh
terraform -chdir=grantstack-backend/terraform plan \
  -var-file=env/dev.tfvars \
  -var='enable_sqs_worker=true' \
  -out=grantstack-dev-worker.tfplan
terraform -chdir=grantstack-backend/terraform apply grantstack-dev-worker.tfplan
```

After the dev work is complete, set `enable_sqs_worker = false` in the dev variable file and apply again. `terraform output -raw sqs_worker_enabled` reports the deployed intent. Processor cold invocations log the queue name, 20-second wait, Lambda-managed idle behavior, and received batch size.

Lambda event source mappings do not expose a configurable exponential idle-backoff setting. AWS manages their pollers and can keep multiple long polls active even when a queue is empty. Disabling the dev mapping is therefore the reliable zero-idle-request control; adding sleep or jitter inside the Lambda handler would not affect the separate AWS-managed pollers.

## Verify Empty Receives

In CloudWatch Metrics, select `AWS/SQS`, `Queue Metrics`, `NumberOfEmptyReceives`, and the `QueueName` dimension. Use a one-day period, the `Sum` statistic, and the deployment date through the current date. The operations dashboard also includes this metric at one-minute resolution.

CLI example for UTC dates:

```sh
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfEmptyReceives \
  --dimensions Name=QueueName,Value=grantstack-dev-processing \
  --statistics Sum \
  --period 86400 \
  --start-time 2026-06-01T00:00:00Z \
  --end-time 2026-07-01T00:00:00Z
```

After disabling the mapping, confirm that daily sums fall to zero after any in-flight polls finish. Also verify the mapping state:

```sh
aws lambda list-event-source-mappings \
  --function-name grantstack-dev-processor \
  --event-source-arn "$(terraform -chdir=grantstack-backend/terraform output -raw processing_queue_arn)"
```
