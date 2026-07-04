variable "project_id" {
  description = "GCP project ID that owns the resources."
  type        = string
}

variable "region" {
  description = "Default region for buckets and the BigQuery dataset."
  type        = string
  default     = "us-west1"
}

variable "bigquery_dataset" {
  description = "BigQuery dataset for cleaned + analytical tables."
  type        = string
  default     = "event_demand_analytics"
}

variable "ticketmaster_api_key" {
  description = "Ticketmaster Discovery API consumer key. Set it in terraform.tfvars (git-ignored); Terraform stores it in Secret Manager for the daily extract function."
  type        = string
  sensitive   = true
}

variable "ticketmaster_schedule" {
  description = "Cron schedule for the Ticketmaster extract (interpreted in America/Los_Angeles). tm_observations is daily-grain, so intra-day sweeps beyond the second only add capture provenance; 2 runs/day keeps union-presence robustness while freeing ~3.1k of the 5k daily call quota (docs/collection_efficiency_review.md, D2). Each run must still complete a full nationwide sweep within MAX_CALLS_PER_RUN."
  type        = string
  default     = "0 6,18 * * *" # twice daily (cut from every-4h on 2026-07-04; live scheduler updated via gcloud)
}

variable "gold_refresh_schedule" {
  description = "Cron schedule for the G1 gold-refresh job (interpreted in America/Los_Angeles). Runs after the day's Ticketmaster extracts so the spine is fresh; once daily is plenty for a precomputed-forecast product."
  type        = string
  default     = "0 9 * * *" # 09:00 LA daily, after the overnight TM extracts
}

variable "alert_email" {
  description = "Email address that receives pipeline failure alerts (set in terraform.tfvars)."
  type        = string
}

variable "force_destroy_buckets" {
  description = "Allow `terraform destroy` to delete buckets that still contain objects. Safe for a class project; flip to false before prod."
  type        = bool
  default     = true
}
