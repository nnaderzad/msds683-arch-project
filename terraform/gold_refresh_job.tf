# G1 — gold-refresh Cloud Run Job (dbt + forecast, validated):
#   Cloud Scheduler --(OAuth :run)--> Cloud Run Job --> refreshes silver + gold + forecast
#
# One execution runs pipeline/gold_refresh.py end-to-end (silver A1/A2/A3 -> dbt build
# -> D2 forecast export -> GX forecast gate), so the three source signals, gold, and the
# forecast all advance in one consistent snapshot. The image is built by Cloud Build from
# pipeline/gold_refresh.Dockerfile and pushed to Artifact Registry; the job runs as a
# dedicated least-privilege service account (BigQuery data editor + job user). Auth is
# ADC — no keys in the image (dbt profiles.yml uses method: oauth -> ADC).

locals {
  gold_refresh_repo  = "jobs"
  gold_refresh_job   = "gold-refresh"
  gold_refresh_image = "${var.region}-docker.pkg.dev/${var.project_id}/${local.gold_refresh_repo}/${local.gold_refresh_job}"

  # Rebuild the image only when the job's source actually changes. Hash the code the
  # container runs (transforms, model, dbt models/config, GX, common) + the build inputs.
  gold_refresh_src_files = sort(setunion(
    fileset("${path.module}/..", "pipeline/**/*.py"),
    fileset("${path.module}/..", "model/**/*.py"),
    fileset("${path.module}/..", "common/**/*.py"),
    fileset("${path.module}/..", "great_expectations/**/*.py"),
    fileset("${path.module}/..", "dbt/models/**"),
    [
      "pipeline/gold_refresh.Dockerfile",
      "pipeline/gold_refresh.requirements.txt",
      "pipeline/gold_refresh.cloudbuild.yaml",
      "dbt/dbt_project.yml",
      "dbt/profiles.yml",
      "dbt/packages.yml",
    ],
  ))
  gold_refresh_src_hash = substr(
    sha1(join(",", [for f in local.gold_refresh_src_files : filemd5("${path.module}/../${f}")])),
    0, 12,
  )
  gold_refresh_image_tag = "${local.gold_refresh_image}:${local.gold_refresh_src_hash}"
}

# ---------------------------------------------------------------------------
# Artifact Registry: home for the job image
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "jobs" {
  location      = var.region
  repository_id = local.gold_refresh_repo
  description   = "Container images for scheduled Cloud Run jobs (gold refresh, etc.)."
  format        = "DOCKER"

  labels = {
    project    = "event-demand-analytics"
    managed_by = "terraform"
  }

  depends_on = [google_project_service.artifactregistry]
}

# ---------------------------------------------------------------------------
# Build + push the image via Cloud Build (re-runs when the source hash changes)
# ---------------------------------------------------------------------------

resource "null_resource" "gold_refresh_image" {
  triggers = {
    src_hash = local.gold_refresh_src_hash
    image    = local.gold_refresh_image_tag
  }

  # Build from the repo root so the whole project is in context (.dockerignore /
  # .gcloudignore keep terraform state + secrets out).
  provisioner "local-exec" {
    working_dir = "${path.module}/.."
    command     = <<-EOT
      gcloud builds submit \
        --project=${var.project_id} \
        --region=${var.region} \
        --config=pipeline/gold_refresh.cloudbuild.yaml \
        --substitutions=_IMAGE=${local.gold_refresh_image_tag}
    EOT
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_project_service.cloudbuild,
  ]
}

# ---------------------------------------------------------------------------
# Service accounts + IAM (least privilege)
# ---------------------------------------------------------------------------

# Identity the job runs as: edits tables in the analytics dataset (silver MERGE,
# dbt CREATE/MERGE, forecast WRITE_TRUNCATE) and runs BigQuery jobs.
resource "google_service_account" "gold_refresh_job" {
  account_id   = "gold-refresh-job"
  display_name = "Gold-refresh Cloud Run job (dbt + forecast)"
  depends_on   = [google_project_service.iam]
}

resource "google_bigquery_dataset_iam_member" "gold_refresh_dataset_editor" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.gold_refresh_job.email}"
}

resource "google_project_iam_member" "gold_refresh_bq_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.gold_refresh_job.email}"
}

# The silver transforms read bronze JSON from the raw bucket and the YouTube channel
# cache from the processed bucket (via gsutil). Read-only, scoped to those two buckets.
resource "google_storage_bucket_iam_member" "gold_refresh_raw_reader" {
  bucket = google_storage_bucket.layers["raw"].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.gold_refresh_job.email}"
}

resource "google_storage_bucket_iam_member" "gold_refresh_processed_reader" {
  bucket = google_storage_bucket.layers["processed"].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.gold_refresh_job.email}"
}

# Identity Cloud Scheduler uses to start the job: invoke-only.
resource "google_service_account" "gold_refresh_scheduler" {
  account_id   = "gold-refresh-scheduler"
  display_name = "Cloud Scheduler invoker for the gold-refresh job"
  depends_on   = [google_project_service.iam]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_job" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.gold_refresh.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gold_refresh_scheduler.email}"
}

# ---------------------------------------------------------------------------
# The Cloud Run Job
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "gold_refresh" {
  name     = local.gold_refresh_job
  location = var.region

  deletion_protection = false

  template {
    template {
      service_account = google_service_account.gold_refresh_job.email
      # The full chain (silver scans + dbt build + train/predict over all events) is a
      # minutes-long batch; give it headroom and never retry a partial write in place.
      timeout     = "3600s"
      max_retries = 0

      containers {
        image = local.gold_refresh_image_tag

        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi" # sklearn fit over the pooled cross-section
          }
        }

        env {
          name  = "DBT_GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "DBT_BQ_DATASET"
          value = google_bigquery_dataset.analytics.dataset_id
        }
        env {
          name  = "DBT_BQ_LOCATION"
          value = var.region
        }
      }
    }
  }

  depends_on = [
    google_project_service.run,
    null_resource.gold_refresh_image,
    google_bigquery_dataset_iam_member.gold_refresh_dataset_editor,
    google_project_iam_member.gold_refresh_bq_jobs,
    google_storage_bucket_iam_member.gold_refresh_raw_reader,
    google_storage_bucket_iam_member.gold_refresh_processed_reader,
  ]
}

# ---------------------------------------------------------------------------
# Cloud Scheduler: the daily trigger (executes the job via the Run Admin API)
# ---------------------------------------------------------------------------

resource "google_cloud_scheduler_job" "gold_refresh" {
  name             = "gold-refresh-daily"
  description      = "Daily silver+gold+forecast refresh (G1) — runs the gold-refresh Cloud Run job."
  region           = var.region
  schedule         = var.gold_refresh_schedule
  time_zone        = "America/Los_Angeles"
  attempt_deadline = "320s" # just the :run kickoff; the job itself runs async

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.gold_refresh.name}:run"

    oauth_token {
      service_account_email = google_service_account.gold_refresh_scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.cloudscheduler,
    google_cloud_run_v2_job_iam_member.scheduler_runs_job,
  ]
}
