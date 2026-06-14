"""Shared Google Cloud Storage helper for landing raw API captures.

Every source POC (ticketmaster / youtube / google_trends) uses this to write its
untouched API output into the BRONZE (raw) bucket under a consistent layout:

    gs://<raw-bucket>/<source>/dt=<YYYY-MM-DD>/<source>_<UTC-timestamp>.<ext>

Partitioning by `dt=` keeps every daily run as its own snapshot (temporal
richness) and lets BigQuery read a source as a date-partitioned external table.

The raw bucket defaults to the project's bronze bucket but can be overridden with
the GCS_RAW_BUCKET environment variable.

Auth uses Application Default Credentials (ADC) — run `gcloud auth
application-default login` once if a call fails with a credentials error.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from google.cloud import storage

# The data-architecture class project + its bronze bucket (see terraform/).
PROJECT_ID = os.environ.get("GCP_PROJECT", "data-architecture-498123")
DEFAULT_RAW_BUCKET = os.environ.get("GCS_RAW_BUCKET", f"{PROJECT_ID}-raw")


def upload_raw(
    source: str,
    payload,
    *,
    bucket: str = DEFAULT_RAW_BUCKET,
    ext: str = "json",
    suffix: str = "",
    run_ts: datetime | None = None,
) -> str:
    """Write one raw capture to the bronze bucket and return its gs:// URI.

    Args:
        source:  short source name, used as the top-level prefix (e.g. "ticketmaster").
        payload: a dict/list (serialized to JSON) or a pre-formatted str/bytes.
        bucket:  destination bucket (defaults to the project bronze bucket).
        ext:     file extension; "json" sets an application/json content type.
        suffix:  optional tag inserted into the filename for traceability and to
                 avoid collisions when a single run writes many files (e.g. the
                 ticketmaster pipeline tags each state, Google Trends each
                 artist+geo). Empty keeps the original ``<source>_<stamp>`` name.
        run_ts:  capture time (defaults to now, UTC) — controls the dt= partition.
    """
    run_ts = run_ts or datetime.now(timezone.utc)
    dt = run_ts.strftime("%Y-%m-%d")
    stamp = run_ts.strftime("%Y%m%dT%H%M%SZ")
    suffix_part = f"_{suffix}" if suffix else ""
    blob_name = f"{source}/dt={dt}/{source}{suffix_part}_{stamp}.{ext}"

    if isinstance(payload, (str, bytes)):
        data = payload
    else:
        data = json.dumps(payload, ensure_ascii=False, indent=2)

    content_type = "application/json" if ext == "json" else "text/plain"

    client = storage.Client(project=PROJECT_ID)
    blob = client.bucket(bucket).blob(blob_name)
    blob.upload_from_string(data, content_type=content_type)

    uri = f"gs://{bucket}/{blob_name}"
    print(f"[gcs] uploaded raw {source} capture -> {uri}")
    return uri


def healthcheck(bucket: str = DEFAULT_RAW_BUCKET) -> str:
    """Write a tiny object to prove auth + bucket write access work."""
    return upload_raw(
        "_healthcheck",
        {"ok": True, "written_by": "common/gcs_io.py"},
        bucket=bucket,
    )


if __name__ == "__main__":
    healthcheck()
