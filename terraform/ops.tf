# Operational telemetry, kept SEPARATE from the analytical warehouse: the demo
# service's SA gets dataEditor here only, so its read-only stance on
# event_demand_analytics (the text-to-SQL agent's IAM backstop) is untouched.
#
# NOTE for the main-root state holder: these resources were created live on
# 2026-08-09 via the bq CLI (the local state wasn't reachable that night).
# Before your next apply, adopt them instead of recreating:
#   terraform import google_bigquery_dataset.ops projects/<project>/datasets/event_demand_ops
#   terraform import google_bigquery_table.ask_feedback \
#     projects/<project>/datasets/event_demand_ops/tables/ask_feedback
#   terraform import google_bigquery_dataset_iam_member.ops_writer_demo_service \
#     "projects/<project>/datasets/event_demand_ops roles/bigquery.dataEditor serviceAccount:event-demand-api@<project>.iam.gserviceaccount.com"

resource "google_bigquery_dataset" "ops" {
  dataset_id  = "event_demand_ops"
  location    = var.region
  description = "Operational telemetry for the demo service (user feedback on /ask answers). Never analytical data."

  labels = {
    project    = "event-demand-analytics"
    managed_by = "terraform"
  }

  depends_on = [google_project_service.bigquery]
}

resource "google_bigquery_table" "ask_feedback" {
  dataset_id          = google_bigquery_dataset.ops.dataset_id
  table_id            = "ask_feedback"
  description         = "One row per thumbs-up/down on a text-to-SQL answer (streaming inserts from POST /ask_feedback). Mined offline into the eval set / schema-context fixes — never used for online learning."
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "ts"
  }

  schema = jsonencode([
    { name = "ts", type = "TIMESTAMP", mode = "REQUIRED", description = "UTC insert time" },
    { name = "verdict", type = "STRING", mode = "REQUIRED", description = "up | down" },
    { name = "question", type = "STRING", mode = "REQUIRED" },
    { name = "sql", type = "STRING", mode = "NULLABLE", description = "generated SQL as shown to the user" },
    { name = "answer", type = "STRING", mode = "NULLABLE" },
    { name = "dataset", type = "STRING", mode = "NULLABLE", description = "real | synth" },
    { name = "model", type = "STRING", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "NULLABLE", description = "/ask status the user rated (ok | refused | blocked | error)" },
    { name = "latency_ms", type = "INT64", mode = "NULLABLE" },
    { name = "bytes_processed", type = "INT64", mode = "NULLABLE" },
    { name = "client_hash", type = "STRING", mode = "NULLABLE", description = "sha256(client key)[:12] for spam triage — no raw IPs stored" },
  ])
}

resource "google_bigquery_dataset_iam_member" "ops_writer_demo_service" {
  dataset_id = google_bigquery_dataset.ops.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:event-demand-api@${var.project_id}.iam.gserviceaccount.com"
}
