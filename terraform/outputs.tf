output "raw_bucket" {
  description = "GCS bucket for raw API responses (bronze layer)."
  value       = google_storage_bucket.layers["raw"].name
}

output "processed_bucket" {
  description = "GCS bucket for cleaned/joined records (silver layer)."
  value       = google_storage_bucket.layers["processed"].name
}

output "analytics_bucket" {
  description = "GCS bucket for model-ready features (gold layer)."
  value       = google_storage_bucket.layers["analytics"].name
}

output "bigquery_dataset" {
  description = "Fully-qualified BigQuery dataset id (project:dataset)."
  value       = "${var.project_id}:${google_bigquery_dataset.analytics.dataset_id}"
}
