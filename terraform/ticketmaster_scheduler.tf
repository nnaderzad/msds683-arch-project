# Daily Ticketmaster extract:
#   Cloud Scheduler --(OIDC HTTP POST)--> Cloud Run function (gen2) --> bronze bucket
#
# Source code lives in cloud_functions/ticketmaster_daily/. Terraform zips it,
# uploads it to a small source bucket, and redeploys the function whenever the
# zip's hash changes. The Ticketmaster API key sits in Secret Manager and is
# injected as an env var — it never appears in the function source or image.

locals {
  ticketmaster_fn_dir = "${path.module}/../cloud_functions/ticketmaster_daily"
}

# ---------------------------------------------------------------------------
# Function source: zip + upload
# ---------------------------------------------------------------------------

data "archive_file" "ticketmaster_fn_src" {
  type        = "zip"
  source_dir  = local.ticketmaster_fn_dir
  output_path = "${path.module}/.build/ticketmaster_daily.zip"
}

# Holds the zipped source code of our Cloud Functions (NOT pipeline data —
# raw/processed/analytics buckets hold that). Terraform uploads the zip here;
# Cloud Functions copies it to Google's auto-created gcf-v2-sources-* bucket
# when it builds the container.
resource "google_storage_bucket" "functions_src" {
  name                        = "${var.project_id}-function-code"
  location                    = var.region
  storage_class               = "STANDARD"
  force_destroy               = var.force_destroy_buckets
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  labels = {
    purpose    = "cloud-function-source-archives"
    project    = "event-demand-analytics"
    managed_by = "terraform"
  }

  depends_on = [google_project_service.storage]
}

# The object name embeds the zip hash, so changing the function code creates a
# new object and forces a redeploy.
resource "google_storage_bucket_object" "ticketmaster_fn_src" {
  name   = "ticketmaster_daily/${data.archive_file.ticketmaster_fn_src.output_md5}.zip"
  bucket = google_storage_bucket.functions_src.name
  source = data.archive_file.ticketmaster_fn_src.output_path
}

# ---------------------------------------------------------------------------
# Secret Manager: Ticketmaster API key
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "ticketmaster_api_key" {
  secret_id = "ticketmaster-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

# The key comes from the (git-ignored) terraform.tfvars. Note it also ends up
# in the local terraform.tfstate — acceptable here since state is local and
# git-ignored; use a remote encrypted backend before sharing state.
resource "google_secret_manager_secret_version" "ticketmaster_api_key" {
  secret      = google_secret_manager_secret.ticketmaster_api_key.id
  secret_data = var.ticketmaster_api_key
}

# ---------------------------------------------------------------------------
# Service accounts + IAM (least privilege)
# ---------------------------------------------------------------------------

# Identity the function runs as: can write to the raw bucket and read the key.
resource "google_service_account" "ticketmaster_fn" {
  account_id   = "ticketmaster-extract"
  display_name = "Ticketmaster daily extract function"
  depends_on   = [google_project_service.iam]
}

resource "google_storage_bucket_iam_member" "ticketmaster_fn_raw_writer" {
  bucket = google_storage_bucket.layers["raw"].name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.ticketmaster_fn.email}"
}

resource "google_secret_manager_secret_iam_member" "ticketmaster_fn_reads_key" {
  secret_id = google_secret_manager_secret.ticketmaster_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ticketmaster_fn.email}"
}

# Silver-layer upsert: the function MERGEs deduped events into BigQuery
# (tm_events keyed on event_id), so it needs to edit tables in the dataset
# and run query/load jobs.
resource "google_bigquery_dataset_iam_member" "ticketmaster_fn_dataset_editor" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ticketmaster_fn.email}"
}

resource "google_project_iam_member" "ticketmaster_fn_bq_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.ticketmaster_fn.email}"
}

# Identity Cloud Scheduler uses to call the function: invoke-only.
resource "google_service_account" "ticketmaster_scheduler" {
  account_id   = "ticketmaster-scheduler"
  display_name = "Cloud Scheduler invoker for Ticketmaster extract"
  depends_on   = [google_project_service.iam]
}

# Gen2 functions run on Cloud Run, so invocation is governed by run.invoker
# on the underlying Cloud Run service (same name as the function).
resource "google_cloud_run_v2_service_iam_member" "scheduler_invokes_fn" {
  project  = var.project_id
  location = var.region
  name     = google_cloudfunctions2_function.ticketmaster_daily.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.ticketmaster_scheduler.email}"
}

# ---------------------------------------------------------------------------
# The function itself
# ---------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "ticketmaster_daily" {
  name        = "ticketmaster-daily-extract"
  location    = var.region
  description = "Fetches upcoming Ticketmaster events and lands raw JSON in the bronze bucket."

  build_config {
    runtime     = "python312"
    entry_point = "run"

    source {
      storage_source {
        bucket = google_storage_bucket.functions_src.name
        object = google_storage_bucket_object.ticketmaster_fn_src.name
      }
    }
  }

  service_config {
    available_memory      = "1Gi"
    available_cpu         = "1" # 1Gi memory requires a full CPU on Cloud Run
    timeout_seconds       = 1800 # nationwide sweep takes ~10-15 min
    max_instance_count    = 1    # a scheduled batch job never needs to scale out
    service_account_email = google_service_account.ticketmaster_fn.email

    environment_variables = {
      GCP_PROJECT         = var.project_id
      GCS_RAW_BUCKET      = google_storage_bucket.layers["raw"].name
      BQ_DATASET          = google_bigquery_dataset.analytics.dataset_id
      STATE_CODES         = "ALL" # all 50 states + DC; or e.g. "CA,NY,TX"
      CLASSIFICATION_NAME = "music"
      DAYS_AHEAD          = "180"
      SLICE_DAYS          = "15" # beats the 1,000-event deep-paging cap per slice
      PAGE_SIZE           = "200"
      MAX_PAGES           = "5"
      # Hard stop per run. 2 runs/day x (1 attempt + 1 retry) x 1200 = 4,800
      # worst case, under the 5,000/day quota. Typical runs use ~700-900.
      MAX_CALLS_PER_RUN = "1200"
    }

    secret_environment_variables {
      key        = "TICKETMASTER_API_KEY"
      project_id = var.project_id
      secret     = google_secret_manager_secret.ticketmaster_api_key.secret_id
      version    = "latest"
    }
  }

  depends_on = [
    google_project_service.cloudfunctions,
    google_project_service.cloudbuild,
    google_project_service.run,
    google_project_service.artifactregistry,
    google_secret_manager_secret_version.ticketmaster_api_key,
    google_secret_manager_secret_iam_member.ticketmaster_fn_reads_key,
  ]
}

# ---------------------------------------------------------------------------
# Cloud Scheduler: the daily trigger
# ---------------------------------------------------------------------------

resource "google_cloud_scheduler_job" "ticketmaster_daily" {
  name             = "ticketmaster-daily-extract"
  description      = "Triggers the twice-daily nationwide Ticketmaster raw extract."
  region           = var.region
  schedule         = var.ticketmaster_schedule
  time_zone        = "America/Los_Angeles"
  attempt_deadline = "1800s" # matches the function timeout (Scheduler's max is 30m)

  retry_config {
    # Exactly 1 retry: the MAX_CALLS_PER_RUN quota math assumes at most
    # 2 attempts per scheduled run — raising this can blow the daily quota.
    retry_count          = 1
    min_backoff_duration = "300s"
  }

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.ticketmaster_daily.service_config[0].uri

    oidc_token {
      service_account_email = google_service_account.ticketmaster_scheduler.email
      audience              = google_cloudfunctions2_function.ticketmaster_daily.service_config[0].uri
    }
  }

  depends_on = [google_project_service.cloudscheduler]
}
