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
  description = "Cron for the daily Google Trends refresh (America/Los_Angeles). 11:00 PT: latest start whose worst-case run (RUN_DEADLINE_SECONDS = 5h40m single-stream crawl) still finishes before the 16:30 PT gold-refresh; typical runs (~3-4h) finish ~14:45 PT. Trends content is unaffected by fetch hour (daily series; current partial day is unstable), so later fetching buys nothing (docs/collection_efficiency_review.md, D8)."
  type        = string
  default     = "0 11 * * *" # 11:00 PT daily (D8, 2026-07-07)
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

variable "gtrends_daily_dma_refresh_days" {
  description = "Daily mode: re-pull each tier-1 (Bay Area / EDM) per-DMA daily series once per this many days (0 = tier-1 rotation off). One iot call returns the full ~269-day daily window, so a multi-day cycle keeps every series current at a fraction of the per-day call cost."
  type        = number
  default     = 4
}

variable "gtrends_snapshot_every_days" {
  description = "Daily mode: rotate 1-in-N of the roster's all-DMA snapshots per day (1 = every artist every day). The geographic distribution moves slowly; thinning frees budget for the tier-1 per-DMA rotation."
  type        = number
  default     = 2
}

variable "gtrends_image_tag" {
  description = "Tag of the Google Trends job image in Artifact Registry."
  type        = string
  default     = "latest"
}

variable "youtube_schedule" {
  description = "Cron for the daily YouTube snapshot (America/Los_Angeles). 15:00 PT: as late as possible (freshest same-day channel stats) while still landing in the same UTC day (PT 01:00-15:59 -> one UTC day) and finishing (~30 min) well before the 16:30 PT gold-refresh (docs/collection_efficiency_review.md, D8)."
  type        = string
  default     = "0 15 * * *" # 15:00 PT daily (D8, 2026-07-07)
}

variable "nineteenhz_schedule" {
  description = "Cron for the daily 19hz listing pull + ticket-page poll (America/Los_Angeles). 08:00 PT: listings are edited by promoters overnight/morning, the polite poller (~10 min) finishes hours before the 16:30 PT gold-refresh, and 08:00 PT (15:00/16:00 UTC) keeps the bronze dt= partition in the same UTC day."
  type        = string
  default     = "0 8 * * *" # 08:00 PT daily
}

variable "ra_schedule" {
  description = "Cron for the single daily Resident Advisor listings request (America/Los_Angeles). RA agreement 2026-07-04: strictly ONE automated request per day (also enforced in-code by collect_ra.py's bronze-partition guard) — change the time freely, never the frequency. 08:15 PT: same reasoning as 19hz, offset so the two scene pulls don't start together."
  type        = string
  default     = "15 8 * * *" # 08:15 PT daily
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
