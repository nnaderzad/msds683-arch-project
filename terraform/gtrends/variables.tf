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
  description = "Deterministic min interval (seconds) between Google Trends calls — the global rate cap. 20s => <=3 calls/min. Keep jobs single-stream so this stays global."
  type        = number
  default     = 20
}

variable "gtrends_daily_call_budget" {
  description = "Global ceiling on Trends calls per UTC day (counted from the GCS partition, shared across daily + backfill). 0 = no cap."
  type        = number
  default     = 800
}

variable "gtrends_backfill_tasks" {
  description = "Cloud Run tasks for the backfill. KEEP AT 1: parallel shards run independent streams that defeat the global rate cap and re-trip the 429 throttle."
  type        = number
  default     = 1
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

variable "youtube_schedule" {
  description = "Cron for the daily YouTube snapshot (America/Los_Angeles)."
  type        = string
  default     = "30 9 * * *" # 30 min after the Trends job, to stagger load
}

variable "youtube_top_n" {
  description = "How many top-ranked Ticketmaster touring artists to feed the YouTube collector (unioned with the watchlist)."
  type        = number
  default     = 500
}

variable "youtube_max_artists" {
  description = "Cap on the YouTube artist universe (TM roster + watchlist). Daily stats cost ~2 units/artist; keep 2*cap + resolve budget under the 10,000/day quota."
  type        = number
  default     = 2000
}

variable "youtube_resolve_max_units" {
  description = "YouTube Data API quota budget for channel resolution per run (search.list = 100 units each; daily quota = 10,000)."
  type        = number
  default     = 5000
}
