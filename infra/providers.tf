# Consolidated provider + backend configuration.
# - AWS provider pinned to the 5.x line so a major bump can't silently change
#   resource behavior on the next `init`.
# - Local backend: state lives on disk next to this config. No remote bucket to
#   bootstrap, which is the right call for a single-developer, single-table setup.
#   Revisit (S3 + DynamoDB lock) if more than one person ever runs `apply`.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "local" {}
}

variable "region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

provider "aws" {
  region = var.region
}
