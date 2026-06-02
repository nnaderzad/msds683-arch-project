locals {
  buckets = {
    raw = {
      purpose    = "Bronze layer — raw JSON from SeatGeek/Spotify/Google Trends APIs."
      versioning = true # keep old captures so daily snapshots can be replayed
    }
    processed = {
      purpose    = "Silver layer — cleaned + joined event-artist records."
      versioning = false
    }
    analytics = {
      purpose    = "Gold layer — model-ready features used by BigQuery + notebooks."
      versioning = false
    }
  }
}

resource "google_storage_bucket" "layers" {
  for_each = local.buckets

  name                        = "${var.project_id}-${each.key}"
  location                    = var.region
  storage_class               = "STANDARD"
  force_destroy               = var.force_destroy_buckets
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = each.value.versioning
  }

  labels = {
    layer       = each.key
    project     = "event-demand-analytics"
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.storage]
}
