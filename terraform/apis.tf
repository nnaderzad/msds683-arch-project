# GCP APIs the project needs enabled before resources can be created.
# `terraform apply` will enable them if they aren't already. Don't disable
# on destroy — other resources in the project may depend on them.
resource "google_project_service" "storage" {
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "bigquery" {
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}
