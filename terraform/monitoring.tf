# Failure alerting for the scheduled Ticketmaster pipeline.
#
# Emails var.alert_email whenever an ERROR-severity log appears from either:
#   - the extract function (uncaught exceptions / 5xx, AND partial failures —
#     main.py writes the run summary to stderr when any state fails, the call
#     budget truncates the sweep, or the BigQuery merge fails), or
#   - the Cloud Scheduler job (failed/timed-out attempts, including the case
#     where the function never even started).
#
# Rate-limited to one email per hour so a bad run sends one message, not one
# per state.

resource "google_monitoring_notification_channel" "pipeline_email" {
  display_name = "Event-demand pipeline alerts"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.monitoring]
}

resource "google_monitoring_alert_policy" "ticketmaster_extract_failures" {
  display_name = "Ticketmaster extract failure"
  combiner     = "OR"

  notification_channels = [google_monitoring_notification_channel.pipeline_email.id]

  conditions {
    display_name = "ERROR logs from the extract function or its scheduler job"

    condition_matched_log {
      filter = <<-EOT
        severity>=ERROR AND (
          (resource.type="cloud_run_revision" AND resource.labels.service_name="${google_cloudfunctions2_function.ticketmaster_daily.name}")
          OR
          (resource.type="cloud_scheduler_job" AND resource.labels.job_id="${google_cloud_scheduler_job.ticketmaster_daily.name}")
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

  documentation {
    content = <<-EOT
      The Ticketmaster extract reported a failure. Check the run summary:

        gcloud functions logs read ticketmaster-daily-extract --region=${var.region} --limit=30

      The JSON summary lists failed_states, skipped_states, and silver.error.
      Tracing guide: tests/README.md.
    EOT
  }

  depends_on = [google_project_service.monitoring]
}
