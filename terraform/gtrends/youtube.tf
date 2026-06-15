# YouTube ingestion — daily Cloud Run Job + Scheduler.
#
# Shares the ingestion image (local.gtrends_image) but overrides the container
# command to run youtube_api/collect_youtube.py. Reads the youtube-api-key secret
# (created out-of-band via gcloud; see google_trends_api/README / project notes).
#
# IAM note: I can't set secret-LEVEL IAM (lacking secretmanager.secrets.setIamPolicy),
# so this SA gets secretAccessor at the PROJECT level — broader than ideal (it can
# read other project secrets, e.g. the Ticketmaster key). A dedicated youtube-ingest
# SA at least contains that breadth to the YouTube job. Tighten to a secret-scoped
# binding when an Owner can grant it.

resource "google_service_account" "youtube_ingest" {
  account_id   = "youtube-ingest"
  display_name = "YouTube ingestion job"
}

resource "google_project_iam_member" "youtube_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin" # write bronze + read/write the channel cache
  member  = "serviceAccount:${google_service_account.youtube_ingest.email}"
}

resource "google_project_iam_member" "youtube_bq_data" {
  project = var.project_id
  role    = "roles/bigquery.dataViewer" # read tm_events to build the roster
  member  = "serviceAccount:${google_service_account.youtube_ingest.email}"
}

resource "google_project_iam_member" "youtube_bq_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.youtube_ingest.email}"
}

resource "google_project_iam_member" "youtube_secret" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor" # read youtube-api-key (project-scoped; see note)
  member  = "serviceAccount:${google_service_account.youtube_ingest.email}"
}

resource "google_cloud_run_v2_job" "youtube_daily" {
  name                = "youtube-daily"
  location            = var.region
  deletion_protection = false

  # The secret grant must exist (and propagate) before the job validates its
  # secret_key_ref — otherwise the first create races IAM propagation.
  depends_on = [google_project_iam_member.youtube_secret]

  template {
    template {
      service_account = google_service_account.youtube_ingest.email
      max_retries     = 1
      timeout         = "3600s"

      containers {
        image   = local.gtrends_image
        command = ["python", "/app/youtube_api/collect_youtube.py"]
        resources {
          limits = { cpu = "1", memory = "1Gi" }
        }
        env {
          name  = "TOP_N"
          value = tostring(var.youtube_top_n)
        }
        env {
          name  = "YOUTUBE_MAX_ARTISTS"
          value = tostring(var.youtube_max_artists)
        }
        env {
          name  = "RESOLVE_MAX_UNITS"
          value = tostring(var.youtube_resolve_max_units)
        }
        env {
          name  = "STATES"
          value = "ALL"
        }
        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "GCS_RAW_BUCKET"
          value = local.raw_bucket
        }
        env {
          name  = "GCS_PROCESSED_BUCKET"
          value = "${var.project_id}-processed"
        }
        env {
          name = "YOUTUBE_API_KEY"
          value_source {
            secret_key_ref {
              secret  = "youtube-api-key"
              version = "latest"
            }
          }
        }
      }
    }
  }
}

# Reuses the gtrends-scheduler SA (already has project-level run.invoker).
resource "google_cloud_scheduler_job" "youtube_daily" {
  name             = "youtube-daily"
  description      = "Triggers the daily YouTube popularity snapshot."
  region           = var.region
  schedule         = var.youtube_schedule
  time_zone        = "America/Los_Angeles"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.youtube_daily.name}:run"

    oauth_token {
      service_account_email = google_service_account.gtrends_scheduler.email
    }
  }

  depends_on = [google_project_iam_member.scheduler_runs_jobs]
}
