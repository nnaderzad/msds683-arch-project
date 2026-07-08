# Google Trends ingestion — Artifact Registry + Cloud Run Jobs + Scheduler.
#
# Two jobs share one image (google_trends_api/job.py) and ONE global rate budget.
# Both run as a SINGLE stream (no parallel shards) so TRENDS_SLEEP is the global
# min interval between Trends calls, and both stop gracefully (exit 0) at the shared
# DAILY_CALL_BUDGET (counted from the GCS partition), a wall-clock deadline, or queue
# exhaustion — whichever trips first.
#   gtrends-backfill : on-demand deep history pass — national + DMA snapshot (+ optional
#                      per-DMA daily). Single stream; budget-capped pass trickles over days.
#   gtrends-daily    : scheduled refresh — soonest-show-first, time-boxed to a small slice.
#
# Existing shared infra (raw bucket, BigQuery dataset, enabled APIs) is referred
# to by NAME, not as resources — this root only creates the new Trends pieces, so
# it never collides with the main root's state.
#
# IAM note: Tomas has projectIamAdmin (project-level setIamPolicy) but NOT
# bucket/dataset-level setIamPolicy, so the runtime SA's roles are bound at the
# PROJECT level (broader than bucket-scoped, fine for a team project).
#
# Deploy: see google_trends_api/DEPLOY.md.

locals {
  raw_bucket    = "${var.project_id}-raw"
  gtrends_image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.gtrends.repository_id}/job:${var.gtrends_image_tag}"

  common_env = {
    GCP_PROJECT       = var.project_id
    GCS_RAW_BUCKET    = local.raw_bucket
    TOP_N             = tostring(var.gtrends_top_n)
    TRENDS_SLEEP      = tostring(var.gtrends_sleep_seconds)     # global min interval between calls
    DAILY_CALL_BUDGET = tostring(var.gtrends_daily_call_budget) # global calls/UTC-day ceiling (both jobs)
    STATES            = "ALL"
  }
}

# --- Image registry ---------------------------------------------------------

resource "google_artifact_registry_repository" "gtrends" {
  location      = var.region
  repository_id = "gtrends"
  format        = "DOCKER"
  description   = "Container images for the Google Trends ingestion jobs."
}

# --- Runtime identity + least-privilege roles (project-level; see IAM note) --

resource "google_service_account" "gtrends_ingest" {
  account_id   = "gtrends-ingest"
  display_name = "Google Trends ingestion jobs"
}

resource "google_project_iam_member" "gtrends_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin" # write bronze JSON (+ list for the resume checkpoint)
  member  = "serviceAccount:${google_service_account.gtrends_ingest.email}"
}

resource "google_project_iam_member" "gtrends_bq_data" {
  project = var.project_id
  role    = "roles/bigquery.dataViewer" # read tm_events to build the roster
  member  = "serviceAccount:${google_service_account.gtrends_ingest.email}"
}

resource "google_project_iam_member" "gtrends_bq_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser" # run the roster query
  member  = "serviceAccount:${google_service_account.gtrends_ingest.email}"
}

# --- Jobs -------------------------------------------------------------------

resource "google_cloud_run_v2_job" "gtrends_backfill" {
  name                = "gtrends-backfill"
  location            = var.region
  deletion_protection = false

  template {
    # KEEP single-stream (gtrends_backfill_tasks=1): parallel shards run independent
    # request streams that defeat the global rate cap and re-trip the 429 throttle.
    parallelism = var.gtrends_backfill_tasks
    task_count  = var.gtrends_backfill_tasks

    template {
      service_account = google_service_account.gtrends_ingest.email
      max_retries     = 1
      timeout         = "86400s" # up to 24h; a single-stream deep pass runs long

      containers {
        image = local.gtrends_image
        resources {
          limits = { cpu = "1", memory = "1Gi" }
        }
        env {
          name  = "JOB_MODE"
          value = "backfill"
        }
        # Cheap-first toggle: false = national + DMA snapshot only; true adds the
        # deep per-DMA daily series. Flip via -var and re-apply for the deep pass.
        env {
          name  = "INCLUDE_DMA"
          value = tostring(var.gtrends_backfill_include_dma)
        }
        # Stop ~just under the 24h task timeout so the run exits 0 (not SIGKILL'd).
        env {
          name  = "RUN_DEADLINE_SECONDS"
          value = "84000"
        }
        # Cross-day resume: skip units landed in the last 30 days so a budget-capped
        # deep pass advances to new units each run instead of redoing the head.
        env {
          name  = "RESUME_LOOKBACK_DAYS"
          value = "30"
        }
        dynamic "env" {
          for_each = local.common_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
}

resource "google_cloud_run_v2_job" "gtrends_daily" {
  name                = "gtrends-daily"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.gtrends_ingest.email
      # Single stream, soonest-show-first, best-effort: it lands what fits before
      # RUN_DEADLINE_SECONDS or the shared daily budget trips, exits 0, resumes
      # tomorrow. The old 3300s deadline capped runs at ~165 of the 800 budgeted
      # calls/day (28-145 files/day observed) — the queue never finished. At 20s a
      # ~665-unit daily queue (national + thinned snapshots + tier-1 DMA rotation)
      # needs ~3.7h, so the deadline is now ~5h40m with a 6h SIGKILL backstop
      # (docs/collection_efficiency_review.md, D3).
      max_retries = 2
      timeout     = "21600s"

      containers {
        image = local.gtrends_image
        resources {
          limits = { cpu = "1", memory = "1Gi" }
        }
        env {
          name  = "JOB_MODE"
          value = "daily"
        }
        env {
          name  = "MAX_UNITS"
          value = tostring(var.gtrends_daily_max_units)
        }
        # Stop starting units ~20 min under the 6h timeout so the run exits 0
        # (not SIGKILL'd); the global DAILY_CALL_BUDGET still caps total API hits.
        env {
          name  = "RUN_DEADLINE_SECONDS"
          value = "20400"
        }
        # Daily wants a fresh same-day refresh, so no cross-day resume (today only).
        env {
          name  = "RESUME_LOOKBACK_DAYS"
          value = "0"
        }
        # Tier-1 (Bay Area DMA 807 + EDM artists) per-DMA daily series, each pair
        # re-pulled once per N days — feeds fact_trends_daily continuously.
        env {
          name  = "DAILY_DMA_REFRESH_DAYS"
          value = tostring(var.gtrends_daily_dma_refresh_days)
        }
        # All-DMA snapshots are slow-moving; rotate 1-in-N of the roster per day.
        env {
          name  = "SNAPSHOT_EVERY_DAYS"
          value = tostring(var.gtrends_snapshot_every_days)
        }
        dynamic "env" {
          for_each = local.common_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
}

# --- Scheduler: trigger the daily job ---------------------------------------

resource "google_service_account" "gtrends_scheduler" {
  account_id   = "gtrends-scheduler"
  display_name = "Cloud Scheduler invoker for the Google Trends daily job"
}

# Scheduler must run (execute) the daily job. Job-level IAM
# (run.jobs.setIamPolicy) isn't available to us — projectIamAdmin is
# project-scoped only — so grant run.invoker (which includes run.jobs.run) at the
# PROJECT level. Broader than job-scoped, acceptable for this team project.
resource "google_project_iam_member" "scheduler_runs_jobs" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.gtrends_scheduler.email}"
}

resource "google_cloud_scheduler_job" "gtrends_daily" {
  name             = "gtrends-daily"
  description      = "Triggers the daily Google Trends refresh + now-7d hourly capture."
  region           = var.region
  schedule         = var.gtrends_schedule
  time_zone        = "America/Los_Angeles"
  attempt_deadline = "320s" # the :run POST returns immediately; the job runs async

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    # Cloud Run Admin API "run a job" endpoint (regional). oauth_token (not OIDC)
    # because the target is a Google API; default scope is cloud-platform.
    uri = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.gtrends_daily.name}:run"

    oauth_token {
      service_account_email = google_service_account.gtrends_scheduler.email
    }
  }
}

# --- Monitoring: alert on job failures --------------------------------------

resource "google_monitoring_notification_channel" "gtrends_email" {
  display_name = "Google Trends pipeline alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_alert_policy" "gtrends_job_failures" {
  display_name = "Google Trends job failure"
  combiner     = "OR"

  notification_channels = [google_monitoring_notification_channel.gtrends_email.id]

  conditions {
    display_name = "ERROR logs from the Google Trends Cloud Run jobs"

    condition_matched_log {
      filter = <<-EOT
        severity>=ERROR AND resource.type="cloud_run_job" AND (
          resource.labels.job_name="${google_cloud_run_v2_job.gtrends_backfill.name}"
          OR resource.labels.job_name="${google_cloud_run_v2_job.gtrends_daily.name}"
        )
      EOT
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
    auto_close = "86400s"
  }
}

# Failed EXECUTIONS of ANY Cloud Run job in the project (gold-refresh, gtrends,
# youtube, 19hz, RA, future ones). Metric-based, so it also catches failures that
# never log an ERROR line (timeout, OOM, image pull). Closes the incident-log gap
# from 2026-07-05..08: gold-refresh failed nightly at a dbt test, fact tables kept
# advancing, and the log-based policy above (scoped to the gtrends job names)
# never fired — the forecast sat stale for 3 days before a human noticed.
# "Freshness != health — alert on execution status."
resource "google_monitoring_alert_policy" "job_execution_failures" {
  display_name = "Cloud Run job execution failed (any job)"
  combiner     = "OR"

  notification_channels = [google_monitoring_notification_channel.gtrends_email.id]

  conditions {
    display_name = "completed_execution_count{result=failed} > 0"

    condition_threshold {
      filter = <<-EOT
        resource.type = "cloud_run_job"
        AND metric.type = "run.googleapis.com/job/completed_execution_count"
        AND metric.labels.result = "failed"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.labels.job_name"]
      }

      trigger {
        count = 1
      }
    }
  }

  # No notification_rate_limit here: the Monitoring API only allows it on
  # log-based conditions; metric policies notify once per incident anyway.
  alert_strategy {
    auto_close = "86400s"
  }
}
