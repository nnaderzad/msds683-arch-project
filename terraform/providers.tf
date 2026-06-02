terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # State is stored locally (terraform.tfstate). For a multi-person setup,
  # uncomment + create the GCS backend bucket manually, then `terraform init
  # -migrate-state`.
  #
  # backend "gcs" {
  #   bucket = "data-architecture-498123-tfstate"
  #   prefix = "arch_project"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
