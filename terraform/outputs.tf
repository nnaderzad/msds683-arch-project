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

output "gold_refresh_job" {
  description = "Cloud Run job that refreshes silver + gold + forecast (G1). Run on demand with: gcloud run jobs execute gold-refresh --region <region>."
  value       = google_cloud_run_v2_job.gold_refresh.name
}

output "gold_refresh_image" {
  description = "Artifact Registry image tag the gold-refresh job runs (rebuilt on source change)."
  value       = local.gold_refresh_image_tag
}

output "gold_refresh_scheduler_job" {
  description = "Cloud Scheduler job that triggers the daily gold refresh."
  value       = google_cloud_scheduler_job.gold_refresh.id
}
