# Single-table store shared by GitHub Actions (predictions, submissions) and
# local tooling (preferences/overrides). PAY_PER_REQUEST = no idle cost; you pay
# per request instead of for provisioned capacity, which is the cheapest option
# for bursty, low-volume access like a CI job that runs a few times a day.
# Provider/backend config lives in providers.tf.
# Apply:  terraform -chdir=infra init && terraform -chdir=infra apply
#
# IAM NOTE: the GitHub Actions OIDC provider and the role CI assumes
# (arn:aws:iam::123456789012:role/github-actions-la-polla-del-miedo, inline
# policy `github-actions-la-polla-del-miedo-scoped` granting dynamodb:* on
# `la-polla-del-miedo*`) are managed OUTSIDE this terraform, by hand in the
# 123456789012 account (placeholder id in this public mirror). This config therefore manages ONLY the table. Keep the
# table name prefixed `la-polla-del-miedo` so the role's scoped policy covers it.

resource "aws_dynamodb_table" "polla" {
  name         = "la-polla-del-miedo"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk" # e.g. MATCH#<id>, POOL#config, STANDINGS#<date>
  range_key    = "sk" # e.g. PREDICTION, OVERRIDE, SUBMISSION#<ts>

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  tags = {
    project = "la-polla-del-miedo"
  }
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table."
  value       = aws_dynamodb_table.polla.name
}

output "dynamodb_table_arn" {
  description = "ARN of the DynamoDB table."
  value       = aws_dynamodb_table.polla.arn
}
