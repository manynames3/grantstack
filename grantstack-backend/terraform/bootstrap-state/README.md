# Terraform Remote State Bootstrap

This directory creates the S3 bucket used by the main GrantStack Terraform stack for remote state.

Run once per AWS account:

```sh
terraform -chdir=grantstack-backend/terraform/bootstrap-state init
terraform -chdir=grantstack-backend/terraform/bootstrap-state apply
```

Then initialize the main stack with the matching backend config:

```sh
terraform -chdir=grantstack-backend/terraform init \
  -backend-config=backend/dev.hcl \
  -migrate-state \
  -force-copy
```

The main backend uses S3 server-side encryption, bucket versioning, public-access blocking, and Terraform's S3 lockfile support.
