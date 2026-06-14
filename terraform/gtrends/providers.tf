# Google Trends ingestion — isolated Terraform root.
#
# Deliberately SEPARATE from the main terraform/ root: the main root uses local
# state (on a teammate's machine) and requires the Ticketmaster API key, so it
# can't be applied cleanly from another laptop. This root creates ONLY the new
# Google Trends resources, refers to the shared bucket/dataset by name, and keeps
# its state in a remote GCS backend — so any team member can plan/apply it.
#
# (The main root can migrate to this same bucket later, under a different prefix,
# to unify everything; see providers.tf in the parent dir.)

terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  backend "gcs" {
    bucket = "data-architecture-498123-tfstate"
    prefix = "gtrends"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
