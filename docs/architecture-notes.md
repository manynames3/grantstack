# GrantStack AWS Architecture Notes

## Request flow

GrantStack uses a serverless, asynchronous request flow for fast intake and low idle cost.

1. The user submits expansion-project facts from the Cloudflare Pages frontend.
2. Amazon API Gateway HTTP API receives `POST /projects` and invokes the Ingestion Lambda.
3. The Ingestion Lambda validates the payload, creates a `project_id` and private access token, writes the accepted record to DynamoDB, sends the work item to SQS, and returns `202 Accepted`.
4. SQS invokes the Processor Lambda asynchronously with a batch size of one.
5. The Processor Lambda reads the active source catalog from S3 when enabled, falls back to the embedded catalog when needed, applies source-backed eligibility checks, and writes the structured report to DynamoDB.
6. The frontend polls `GET /projects/{project_id}?token=...` through API Gateway; the Report Lambda validates the token and returns status/report data from DynamoDB.
7. The frontend posts privacy-light analytics events to `POST /analytics`; the Analytics Lambda stores them in a separate DynamoDB table with TTL.
8. EventBridge invokes the Source Refresh Lambda on a schedule so official source URL metadata can be refreshed into the S3 catalog.

## CI/CD flow

The repository uses GitHub Actions for validation on push and pull request. The workflow checks Terraform formatting, initializes Terraform without a backend, validates the Terraform stack, compiles Python Lambda handlers and scripts, installs the minimal Python test dependency, and runs the backend unit test suite.

Deployments are intentionally explicit rather than fully automated: an operator initializes the selected Terraform backend config, reviews a plan, and applies it to the target environment. This keeps dev, staging, and prod state isolated while avoiding accidental infrastructure changes from every commit.

## Security boundaries

- API Gateway fronts the Lambda integrations and can enable a JWT authorizer for `POST /projects` in staging/prod.
- Each Lambda has a separate IAM role and narrowly scoped permissions for its job.
- The Ingestion Lambda can write accepted records and send SQS messages.
- The Processor Lambda can consume SQS messages, write project reports, read the S3 catalog when source refresh is enabled, and read only configured Secrets Manager ARNs for optional provider mode.
- The Report Lambda can read project records but does not write them.
- The Analytics Lambda writes only to the analytics table.
- The Source Refresh Lambda can read/write only the configured S3 catalog object.
- SQS uses server-side encryption, DynamoDB uses server-side encryption, and S3 catalog/remote-state buckets are encrypted with public access blocked.
- Private report access uses per-project access tokens.

## Observability and logging

The stack includes a practical operations baseline: CloudWatch log groups with retention, API Gateway access logs when enabled, Lambda error alarms, SQS queue-age and DLQ visible-message alarms, a processor failure metric filter, a CloudWatch dashboard, and X-Ray tracing when enabled.

The dashboard focuses on the signals a reviewer or operator would care about first: API traffic/errors/latency, Lambda errors and p95 duration, SQS queue health, DynamoDB on-demand activity, analytics activity, and source refresh health.

## Cost controls

GrantStack is designed for near-zero idle compute cost. API Gateway HTTP API, Lambda, SQS, DynamoDB `PAY_PER_REQUEST`, S3, and Cloudflare Pages avoid always-on application servers. The stack also uses API throttling, DynamoDB TTL for project and analytics records, S3 lifecycle rules for old catalog versions, explicit log retention, and optional CloudWatch alarms/dashboard controls.

There can still be small storage and observability charges from retained logs, X-Ray traces, S3 objects, DynamoDB data, CloudWatch alarms/dashboards, and Terraform remote state.

## Teardown strategy

The repo supports Terraform-managed teardown through the same backend/environment separation used for deployment.

Recommended teardown path:

```sh
cd grantstack-backend/terraform
terraform init -reconfigure -backend-config=backend/dev.hcl
terraform destroy
```

For staging or prod, use the matching backend config and variable file. Remote state is bootstrapped separately, so destroy the application stack before deciding whether to remove the shared state bucket. Data-bearing resources such as DynamoDB tables and S3 catalog/remote-state buckets should be reviewed before teardown because they may hold project reports, analytics records, catalog metadata, and state history.
