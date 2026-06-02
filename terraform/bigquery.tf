resource "google_bigquery_dataset" "analytics" {
  dataset_id  = var.bigquery_dataset
  location    = var.region
  description = "Cleaned + analytical tables for the event demand forecasting project (SeatGeek + Spotify + Google Trends)."

  labels = {
    project    = "event-demand-analytics"
    managed_by = "terraform"
  }

  depends_on = [google_project_service.bigquery]
}
