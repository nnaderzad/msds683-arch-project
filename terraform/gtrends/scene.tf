# Scene-listing ingestion (19hz.info + Resident Advisor) — daily Cloud Run Jobs
# + Schedulers. Both reuse the shared ingestion image (local.gtrends_image) with
# a command override, like youtube.tf.
#
# These collectors write BRONZE ONLY (no BQ) — silver parsing is a follow-on —
# so their SA gets just the raw-bucket storage role. The RA job additionally
# LISTS the bucket: collect_ra.py's one-request-per-day guard checks whether
# today's ra/dt= partition already exists before making its single permitted
# GraphQL call (written RA agreement, 2026-07-04 — never raise the frequency).

resource "google_service_account" "scene_ingest" {
  account_id   = "scene-ingest"
  display_name = "19hz + RA scene-listing ingestion jobs"
}

resource "google_project_iam_member" "scene_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin" # write bronze + list (RA daily guard)
  member  = "serviceAccount:${google_service_account.scene_ingest.email}"
}

resource "google_cloud_run_v2_job" "nineteenhz_daily" {
  name                = "nineteenhz-daily"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.scene_ingest.email
      max_retries     = 1
      timeout         = "1800s" # listing fetch is seconds; the polite ticket-page poll ~10 min

      containers {
        image = local.gtrends_image
        # Runs collect_19hz.py then poll_ticket_pages.py (see nineteenhz_api/job.py).
        command = ["python", "/app/nineteenhz_api/job.py"]
        resources {
          limits = { cpu = "1", memory = "512Mi" }
        }
        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "GCS_RAW_BUCKET"
          value = local.raw_bucket
        }
      }
    }
  }
}

resource "google_cloud_run_v2_job" "ra_daily" {
  name                = "ra-daily"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.scene_ingest.email
      # A Cloud-Run-level retry only re-runs after a FAILED attempt (no bronze
      # landed), which the RA agreement's own retry carve-out permits; the
      # in-code guard blocks any second request once a response has landed.
      max_retries = 1
      timeout     = "600s" # one GraphQL POST

      containers {
        image   = local.gtrends_image
        command = ["python", "/app/ra_api/collect_ra.py", "--land-raw"]
        resources {
          limits = { cpu = "1", memory = "512Mi" }
        }
        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "GCS_RAW_BUCKET"
          value = local.raw_bucket
        }
      }
    }
  }
}

# Reuses the gtrends-scheduler SA (already has project-level run.invoker).
resource "google_cloud_scheduler_job" "nineteenhz_daily" {
  name             = "nineteenhz-daily"
  description      = "Triggers the daily 19hz listing pull + ticket-page availability poll."
  region           = var.region
  schedule         = var.nineteenhz_schedule
  time_zone        = "America/Los_Angeles"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.nineteenhz_daily.name}:run"

    oauth_token {
      service_account_email = google_service_account.gtrends_scheduler.email
    }
  }

  depends_on = [google_project_iam_member.scheduler_runs_jobs]
}

resource "google_cloud_scheduler_job" "ra_daily" {
  name             = "ra-daily"
  description      = "Triggers the single daily Resident Advisor listings request (RA agreement: 1/day)."
  region           = var.region
  schedule         = var.ra_schedule
  time_zone        = "America/Los_Angeles"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.ra_daily.name}:run"

    oauth_token {
      service_account_email = google_service_account.gtrends_scheduler.email
    }
  }

  depends_on = [google_project_iam_member.scheduler_runs_jobs]
}
