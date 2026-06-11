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

output "ticketmaster_function_uri" {
  description = "HTTPS endpoint of the daily Ticketmaster extract function."
  value       = google_cloudfunctions2_function.ticketmaster_daily.service_config[0].uri
}

output "ticketmaster_scheduler_job" {
  description = "Cloud Scheduler job that triggers the daily Ticketmaster extract."
  value       = google_cloud_scheduler_job.ticketmaster_daily.id
}
