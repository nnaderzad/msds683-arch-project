# Google Trends ingestion — Artifact Registry + Cloud Run Jobs + Scheduler.
#
# Two jobs share one image (google_trends_api/job.py):
#   gtrends-backfill : deep history pass — national + DMA snapshot (+ optional
#                      per-DMA daily). Run ON DEMAND, sharded, long timeout.
#   gtrends-daily    : scheduled refresh — national + DMA snapshot + the now-7d
#                      HOURLY capture (the one resolution Google can't backfill).
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
    GCP_PROJECT    = var.project_id
    GCS_RAW_BUCKET = local.raw_bucket
    TOP_N          = tostring(var.gtrends_top_n)
    TRENDS_SLEEP   = tostring(var.gtrends_sleep_seconds)
    STATES         = "ALL"
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
    parallelism = var.gtrends_backfill_tasks
    task_count  = var.gtrends_backfill_tasks

    template {
      service_account = google_service_account.gtrends_ingest.email
      max_retries     = 1
      timeout         = "86400s" # up to 24h; a sharded deep backfill runs long

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
      max_retries     = 1
      timeout         = "3600s"

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
