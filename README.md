# GrantStack

GrantStack is a serverless project-intake and grant-screening product for capex-heavy facility expansion projects. A user submits project basics through the landing page, the API accepts the request immediately, asynchronous workers generate a source-backed incentive-screening memo, and the user views the result through a private tokenized report link.

## Live Demo

- Live site: https://grantstack.pages.dev
- GitHub repository: https://github.com/manynames3/grantstack

## Live Surfaces

- Landing page: https://grantstack.pages.dev
- API index: https://rx967db2q9.execute-api.us-east-1.amazonaws.com/projects
- Submit endpoint: `POST https://rx967db2q9.execute-api.us-east-1.amazonaws.com/projects`
- Private report endpoint: `GET /projects/{project_id}?token={access_token}`

## Current Readiness

This is now ready for credible paid-pilot conversations. The core workflow is live:

1. Static Cloudflare Pages landing page collects project specs.
2. API Gateway accepts project submissions.
3. Ingestion Lambda validates payloads, creates a project record, issues an access token, and queues work.
4. SQS triggers the processor Lambda asynchronously.
5. Processor Lambda screens the project against the active S3-backed incentive source catalog and writes the completed structured report to DynamoDB On-Demand.
6. The report page polls the private report endpoint and renders the result with citations, diligence questions, and print/export controls.

Important limitation: the deployed dev backend is configured with `mock_external_calls = true`, which means it uses the source-backed evidence engine instead of paid external vector/LLM calls. Staging and production examples are included for OpenAI/Anthropic-compatible LLM calls, OpenAI embeddings, and Pinecone Serverless retrieval; activating them requires Secrets Manager ARNs and a live vector index.

## Repository Layout

- `grantstack-backend/ARCHITECTURE.md` - architecture blueprint and operational notes.
- `grantstack-backend/terraform/` - AWS serverless infrastructure.
- `grantstack-backend/lambda/` - Python Lambda handlers.
- `grantstack-backend/scripts/smoke_test.py` - public API workflow smoke test.
- `grantstack-backend/docs/DLQ_REPLAY_RUNBOOK.md` - dead-letter queue recovery runbook.
- `grantstack-landing/` - Cloudflare Pages static site.

## Backend Deploy

Prerequisites:

- AWS credentials with permissions to manage API Gateway, Lambda, SQS, DynamoDB, IAM, and CloudWatch.
- Terraform installed.
- Python 3 available locally.

Bootstrap remote state once per AWS account:

```sh
terraform -chdir=grantstack-backend/terraform/bootstrap-state init
terraform -chdir=grantstack-backend/terraform/bootstrap-state apply
```

Deploy dev with the encrypted S3 backend:

```sh
cd grantstack-backend/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config=backend/dev.hcl
terraform plan -out=grantstack.tfplan
terraform apply grantstack.tfplan
```

Staging and production use isolated state keys and variable files:

```sh
terraform init -reconfigure -backend-config=backend/staging.hcl
cp env/staging.tfvars.example env/staging.tfvars
terraform plan -var-file=env/staging.tfvars

terraform init -reconfigure -backend-config=backend/prod.hcl
cp env/prod.tfvars.example env/prod.tfvars
terraform plan -var-file=env/prod.tfvars
```

Validate:

```sh
python3 -m py_compile ../lambda/ingest_handler.py ../lambda/processor_handler.py ../lambda/report_handler.py ../lambda/source_refresh_handler.py
python3 -m unittest discover -s ../tests
terraform fmt -check
terraform validate
../scripts/smoke_test.py --timeout 180 --interval 5
```

## Operations

The backend now includes a formal CloudWatch/X-Ray baseline:

- Active X-Ray tracing for ingestion, processor, report, and source-refresh Lambdas.
- Structured HTTP API access logs in `/aws/apigateway/grantstack-dev-http-api`.
- CloudWatch dashboard output `cloudwatch_dashboard_name`, currently `grantstack-dev-operations`.
- Lambda error alarms, queue-age alarms, DLQ alarm, processor failure log metric, and source-refresh error alarm.

The source catalog is stored in S3 and refreshed on an EventBridge schedule:

- S3 bucket output `source_catalog_bucket`.
- Catalog key output `source_catalog_key`.
- Refresh Lambda output `source_refresh_function_name`.
- Default schedule: `rate(7 days)`.

The local JSON catalog remains the authoritative seed for curated program details; the refresh job verifies official source URLs, records status and content hashes, and writes the active catalog to S3 for the processor.

## AI Provider Modes

Dev keeps costs predictable with `mock_external_calls = true`. In that mode, reports are deterministic, cited, and source-backed.

For staging/prod, set `mock_external_calls = false` and provide:

- `llm_provider`: `openai`, `anthropic`, or `generic_json`.
- `llm_api_endpoint`, `llm_api_key_secret_arn`, and `llm_model`.
- `embedding_provider = "openai"`, `embedding_api_key_secret_arn`, and `embedding_model`.
- `vector_db_provider = "pinecone"` with `vector_db_endpoint` and `vector_db_api_key_secret_arn`.

Terraform grants the processor Lambda `secretsmanager:GetSecretValue` only for the configured secret ARNs.

## Frontend Deploy

Prerequisites:

- Cloudflare Wrangler authenticated for the target account.

Deploy:

```sh
npx wrangler pages deploy grantstack-landing --project-name=grantstack --branch=main --commit-dirty=true
```

## API Contract

Submit a project:

```json
{
  "location": "Augusta, GA",
  "capex": 42000000,
  "jobs": 140,
  "facility_type": "advanced manufacturing",
  "contact_email": "pilot@example.com",
  "company_name": "Acme Manufacturing",
  "average_wage": 72000,
  "project_timeline": "Site decision inside 120 days",
  "competing_locations": "SC, TN"
}
```

Successful response:

```json
{
  "project_id": "uuid",
  "access_token": "private-token",
  "status": "ACCEPTED"
}
```

The `access_token` is required to read the private report. A missing token returns `401`; an invalid token returns `403`.

## Cost Posture

The infrastructure is designed for near-zero idle cost:

- API Gateway HTTP API charges only by request.
- API Gateway stage throttling is enabled to reduce accidental or abusive request bursts.
- Lambda charges only during execution.
- SQS charges by request.
- DynamoDB uses `PAY_PER_REQUEST`.
- DynamoDB TTL is enabled on `expires_at` so pilot data can age out automatically.
- CloudWatch Logs, dashboards, alarms, X-Ray traces, S3 catalog storage, and remote Terraform state may create small charges as usage grows.
- Cloudflare Pages static hosting has no always-on application server.

## Next Paid-Product Blockers

- Expand the incentive catalog beyond the initial GA, NC, and federal source set.
- Add customer identity, saved reports, and payment gating for repeat usage.
- Add email delivery for completed report links.
- Add analytics for activation and conversion.
- Add a human-review workflow or disclaimer if reports are sold as decision-support memos.
