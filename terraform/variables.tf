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
  description = "Cron schedule for the Ticketmaster extract (America/Los_Angeles — PT is the project's reference clock, see docs/REPO_STATE.md 'Clock & cadence'). tm_observations is daily-grain, so intra-day sweeps beyond the second only add capture provenance; 2 runs/day keeps union-presence robustness while freeing ~3.1k of the 5k daily call quota (docs/collection_efficiency_review.md, D2/D8). 15:00 PT is the LATEST slot whose capture still lands in the same UTC day year-round (PT 01:00-15:59 -> one UTC day) — keeping snapshot_date joins same-cycle across sources. Each run must still complete a full nationwide sweep within MAX_CALLS_PER_RUN."
  type        = string
  default     = "0 5,15 * * *" # 05:00 PT insurance sweep + 15:00 PT load-bearing sweep (D8, 2026-07-07)
}

variable "gold_refresh_schedule" {
  description = "Cron schedule for the G1 gold-refresh job (America/Los_Angeles). 16:30 PT: after the 15:00 PT TM sweep + YouTube run and the 11:00 PT Trends crawl (typ. done ~14:45 PT), so gold serves SAME-DAY signals from all three sources by ~17:15 PT — comfortably before the 19:00 PT serve-by target (docs/collection_efficiency_review.md, D8)."
  type        = string
  default     = "30 16 * * *" # 16:30 PT daily, after all same-day collections
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
