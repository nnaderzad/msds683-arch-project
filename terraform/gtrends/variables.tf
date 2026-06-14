variable "project_id" {
  description = "GCP project ID that owns the resources."
  type        = string
}

variable "region" {
  description = "Region for Artifact Registry, Cloud Run Jobs, and Scheduler."
  type        = string
  default     = "us-west1"
}

variable "alert_email" {
  description = "Email that receives Google Trends job failure alerts."
  type        = string
}

variable "gtrends_schedule" {
  description = "Cron for the daily Google Trends refresh (America/Los_Angeles)."
  type        = string
  default     = "0 9 * * *" # 9am PT daily
}

variable "gtrends_top_n" {
  description = "How many top-ranked touring artists the roster selects (plus the curated seed)."
  type        = number
  default     = 250
}

variable "gtrends_sleep_seconds" {
  description = "Polite pause between Google Trends calls to avoid HTTP 429."
  type        = number
  default     = 12
}

variable "gtrends_backfill_tasks" {
  description = "Parallel Cloud Run tasks for the backfill (each owns a disjoint queue shard)."
  type        = number
  default     = 4
}

variable "gtrends_backfill_include_dma" {
  description = "Backfill depth: false = national + DMA snapshot only (cheap-first); true adds the deep per-DMA daily series (~7k calls)."
  type        = bool
  default     = false
}

variable "gtrends_daily_max_units" {
  description = "Cap on fetch units per daily run (0 = no cap)."
  type        = number
  default     = 0
}

variable "gtrends_image_tag" {
  description = "Tag of the Google Trends job image in Artifact Registry."
  type        = string
  default     = "latest"
}
