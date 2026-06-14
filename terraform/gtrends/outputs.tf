output "gtrends_image" {
  description = "Artifact Registry image the Google Trends jobs run."
  value       = local.gtrends_image
}

output "gtrends_backfill_job" {
  description = "Cloud Run Job for the deep Google Trends backfill (run on demand)."
  value       = google_cloud_run_v2_job.gtrends_backfill.name
}

output "gtrends_daily_job" {
  description = "Cloud Run Job for the scheduled daily Google Trends refresh."
  value       = google_cloud_run_v2_job.gtrends_daily.name
}

output "gtrends_ingest_sa" {
  description = "Service account the Google Trends jobs run as."
  value       = google_service_account.gtrends_ingest.email
}

output "gtrends_scheduler_job" {
  description = "Cloud Scheduler job that triggers the daily Google Trends refresh."
  value       = google_cloud_scheduler_job.gtrends_daily.id
}
