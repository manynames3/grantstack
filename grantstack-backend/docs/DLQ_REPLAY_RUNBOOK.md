# GrantStack DLQ Replay Runbook

Use this runbook when the `grantstack-*-dlq-visible-messages` alarm fires or the smoke test reports DLQ messages.

## 1. Confirm Scope

```bash
cd terraform
terraform output

aws sqs get-queue-attributes \
  --queue-url "$(terraform output -raw processing_dlq_url)" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

## 2. Inspect a Message

Do not replay until the root cause is understood and fixed.

```bash
aws sqs receive-message \
  --queue-url "$(terraform output -raw processing_dlq_url)" \
  --attribute-names All \
  --message-attribute-names All \
  --max-number-of-messages 1 \
  --wait-time-seconds 20 \
  --visibility-timeout 30
```

Common causes:

- Invalid or expired vector DB / LLM secret.
- External provider timeout or HTTP error.
- Project payload shape changed without updating the processor.
- DynamoDB IAM or table configuration drift.

Check processor logs:

```bash
aws logs tail "/aws/lambda/$(terraform output -raw processor_function_name)" --follow
```

## 3. Fix Forward

Examples:

```bash
# Rotate/update a raw string secret value.
aws secretsmanager put-secret-value \
  --secret-id "$LLM_SECRET_ARN" \
  --secret-string "$NEW_LLM_API_KEY"

# Redeploy after code or infrastructure fixes.
terraform plan
terraform apply
```

## 4. Replay Safely

Replay slowly at first. This uses native SQS DLQ redrive.

```bash
DLQ_ARN="$(terraform output -raw processing_dlq_arn)"
QUEUE_ARN="$(terraform output -raw processing_queue_arn)"

aws sqs start-message-move-task \
  --source-arn "$DLQ_ARN" \
  --destination-arn "$QUEUE_ARN" \
  --max-number-of-messages-per-second 5
```

Monitor progress:

```bash
aws sqs list-message-move-tasks --source-arn "$DLQ_ARN"

aws sqs get-queue-attributes \
  --queue-url "$(terraform output -raw processing_dlq_url)" \
  --attribute-names ApproximateNumberOfMessages
```

## 5. Verify Recovery

```bash
python ../scripts/smoke_test.py
```

Then verify:

- DLQ visible message count is `0`.
- Processing queue oldest-message alarm returns to OK.
- Failed DynamoDB items either completed after replay or have a documented reason to remain failed.
