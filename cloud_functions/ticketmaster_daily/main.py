"""Nationwide Ticketmaster extractor — runs as a Cloud Run function (gen2).

Cloud Scheduler hits this function twice a day (see
terraform/ticketmaster_scheduler.tf). It sweeps every US state (plus DC),
fetches upcoming events from the Ticketmaster Discovery API, and lands one
raw JSON file per state in the bronze (raw) bucket:

    gs://<raw-bucket>/ticketmaster/dt=<YYYY-MM-DD>/ticketmaster_<STATE>_<stamp>.json

The fetch logic mirrors ticketmaster_api/ticketmaster_poc.py, inlined here
because Cloud Functions only uploads this one directory as its source.

Coverage: Ticketmaster caps deep paging at size * page < 1,000, so a single
filter set can never return more than 1,000 events. Each state's DAYS_AHEAD
window is therefore split into disjoint SLICE_DAYS-day slices, and each slice
paged separately — every slice gets its own 1,000-event budget.

API budget: Ticketmaster allows 5,000 calls/day and 5 calls/second.
MAX_CALLS_PER_RUN (default 780) is a hard stop: six scheduled runs/day all
hitting the cap is 4,680 < 5,000. A typical nationwide run uses ~650-750
calls (~4,300/day at 6 runs). The sleep between requests keeps us under the
rate limit.

Failure isolation: each state is fetched and uploaded independently. One
state failing is recorded in the run summary and the sweep continues; the
run only errors out (triggering a Cloud Scheduler retry) if every state
failed, which signals an outage rather than a data blip.

Dedup / upsert (silver layer): repeated runs return the same events, so after
the sweep the run flattens every event and MERGEs into the BigQuery table
<BQ_DATASET>.tm_events keyed on Ticketmaster's event id — existing rows are
updated in place (fresh status/prices, last_seen_ts_utc bumped), new ids are
inserted (first_seen_ts_utc stamped). The bronze files stay append-only
snapshots for replay/history; tm_events always holds exactly one current row
per event.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import functions_framework
from google.cloud import bigquery, storage

BASE_URL = "https://app.ticketmaster.com/discovery/v2"
USER_AGENT = "msds-data-architecture-pipeline/1.0"

# Stay under Ticketmaster's 5 requests/second rate limit.
SECONDS_BETWEEN_CALLS = 0.25

# All 50 states + DC; STATE_CODES env var overrides ("ALL" or unset = this).
ALL_STATE_CODES = (
    "AL,AK,AZ,AR,CA,CO,CT,DE,DC,FL,GA,HI,ID,IL,IN,IA,KS,KY,LA,ME,MD,MA,MI,"
    "MN,MS,MO,MT,NE,NV,NH,NJ,NM,NY,NC,ND,OH,OK,OR,PA,RI,SC,SD,TN,TX,UT,VT,"
    "VA,WA,WV,WI,WY"
)


def state_codes() -> list[str]:
    """States to sweep, from the STATE_CODES env var ('ALL' = nationwide)."""

    configured = os.environ.get("STATE_CODES", "ALL").strip()
    if configured.upper() == "ALL" or not configured:
        configured = ALL_STATE_CODES
    return [code.strip().upper() for code in configured.split(",") if code.strip()]


def fetch_json(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Call one Ticketmaster endpoint and return its JSON response."""

    api_key = os.environ["TICKETMASTER_API_KEY"]  # injected from Secret Manager
    url = f"{BASE_URL}/{endpoint}?{urlencode({'apikey': api_key, **params}, doseq=True)}"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def iso_utc(dt: datetime) -> str:
    """Format datetimes the way Ticketmaster expects: YYYY-MM-DDTHH:MM:SSZ."""

    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def search_state_events(
    state_code: str, now: datetime, calls_remaining: int
) -> tuple[list[dict[str, Any]], int]:
    """Fetch all of one state's events; return (raw_events, api_calls_made).

    The DAYS_AHEAD window is split into disjoint SLICE_DAYS-day slices (each
    slice ends 1s before the next begins — Ticketmaster treats the range as
    inclusive — so no event is fetched twice). Stops early if calls_remaining
    runs out.
    """

    classification = os.environ.get("CLASSIFICATION_NAME", "music")
    days_ahead = int(os.environ.get("DAYS_AHEAD", "180"))
    slice_days = int(os.environ.get("SLICE_DAYS", "15"))
    size = int(os.environ.get("PAGE_SIZE", "200"))
    max_pages = int(os.environ.get("MAX_PAGES", "5"))

    window_end = now + timedelta(days=days_ahead)
    raw_events: list[dict[str, Any]] = []
    calls = 0

    for offset in range(0, days_ahead, slice_days):
        slice_start = now + timedelta(days=offset)
        slice_end = min(slice_start + timedelta(days=slice_days), window_end)
        slice_end -= timedelta(seconds=1)

        for page in range(max_pages):
            if calls >= calls_remaining:
                print(f"[{state_code}] call budget exhausted; stopping early")
                return raw_events, calls

            params: dict[str, Any] = {
                "countryCode": "US",
                "stateCode": state_code,
                "classificationName": classification,
                "startDateTime": iso_utc(slice_start),
                "endDateTime": iso_utc(slice_end),
                "sort": "date,asc",
                "size": size,
                "page": page,
                "includeTBA": "no",
                "includeTBD": "no",
            }

            time.sleep(SECONDS_BETWEEN_CALLS)
            payload = fetch_json("events.json", params)
            calls += 1

            events = payload.get("_embedded", {}).get("events", [])
            raw_events.extend(events)

            page_info = payload.get("page", {})
            current_page = int(page_info.get("number") or page)
            total_pages = int(page_info.get("totalPages") or 0)
            if not events or current_page + 1 >= total_pages:
                break

    return raw_events, calls


def upload_raw(state_code: str, events: list[dict[str, Any]], run_ts: datetime) -> str:
    """Write one state's untouched event JSON to the bronze bucket.

    Same <source>/dt=<date>/ layout as common/gcs_io.upload_raw so BigQuery
    can read all sources as date-partitioned external tables; the state code
    in the filename keeps each capture traceable to its API filter.
    """

    project_id = os.environ["GCP_PROJECT"]
    bucket_name = os.environ["GCS_RAW_BUCKET"]
    dt = run_ts.strftime("%Y-%m-%d")
    stamp = run_ts.strftime("%Y%m%dT%H%M%SZ")
    blob_name = f"ticketmaster/dt={dt}/ticketmaster_{state_code}_{stamp}.json"

    client = storage.Client(project=project_id)
    blob = client.bucket(bucket_name).blob(blob_name)
    blob.upload_from_string(
        json.dumps(events, ensure_ascii=False),
        content_type="application/json",
    )
    return f"gs://{bucket_name}/{blob_name}"


# ---------------------------------------------------------------------------
# Silver layer: flatten + upsert keyed on Ticketmaster's event id
# ---------------------------------------------------------------------------

# One (column, BigQuery type) pair per staging column, in table order. The
# silver table tm_events has the same columns plus first_seen_ts_utc, which
# the MERGE sets once on insert and never updates.
STAGING_SCHEMA: list[tuple[str, str]] = [
    ("event_id", "STRING"),
    ("event_name", "STRING"),
    ("event_type", "STRING"),
    ("event_url", "STRING"),
    ("local_date", "DATE"),
    ("local_time", "STRING"),
    ("date_time_utc", "TIMESTAMP"),
    ("timezone", "STRING"),
    ("status_code", "STRING"),
    ("public_sale_start_utc", "TIMESTAMP"),
    ("public_sale_end_utc", "TIMESTAMP"),
    ("public_sale_start_tbd", "BOOL"),
    ("price_type", "STRING"),
    ("price_currency", "STRING"),
    ("price_min", "FLOAT64"),
    ("price_max", "FLOAT64"),
    ("venue_id", "STRING"),
    ("venue_name", "STRING"),
    ("venue_city", "STRING"),
    ("venue_state_code", "STRING"),
    ("venue_country_code", "STRING"),
    ("venue_postal_code", "STRING"),
    ("venue_address", "STRING"),
    ("venue_latitude", "FLOAT64"),
    ("venue_longitude", "FLOAT64"),
    ("attraction_ids", "STRING"),
    ("attraction_names", "STRING"),
    ("segment", "STRING"),
    ("genre", "STRING"),
    ("subgenre", "STRING"),
    ("last_seen_ts_utc", "TIMESTAMP"),
]

# The python client's SchemaField still expects legacy type names.
_LEGACY_TYPES = {"FLOAT64": "FLOAT", "BOOL": "BOOLEAN"}


def first_price_range(event: dict[str, Any]) -> dict[str, Any]:
    """Pick one price range from the event, preferring standard prices."""

    ranges = event.get("priceRanges") or []
    for item in ranges:
        if item.get("type") == "standard":
            return item
    return ranges[0] if ranges else {}


def primary_classification(event: dict[str, Any]) -> dict[str, Any]:
    """Pick the primary classification if Ticketmaster marks one."""

    classifications = event.get("classifications") or []
    for item in classifications:
        if item.get("primary"):
            return item
    return classifications[0] if classifications else {}


def flatten_event(event: dict[str, Any], run_ts: datetime) -> dict[str, Any]:
    """Turn one nested Ticketmaster event into one staging row (event_id key).

    Same flattening as ticketmaster_api/ticketmaster_poc.py, shaped to match
    STAGING_SCHEMA.
    """

    embedded = event.get("_embedded") or {}
    venues = embedded.get("venues") or []
    attractions = embedded.get("attractions") or []
    venue = venues[0] if venues else {}

    dates = event.get("dates") or {}
    start = dates.get("start") or {}
    status = dates.get("status") or {}
    sales_public = (event.get("sales") or {}).get("public") or {}
    price_range = first_price_range(event)
    classification = primary_classification(event)

    segment = classification.get("segment") or {}
    genre = classification.get("genre") or {}
    subgenre = classification.get("subGenre") or {}

    city = venue.get("city") or {}
    state = venue.get("state") or {}
    country = venue.get("country") or {}
    location = venue.get("location") or {}
    address = venue.get("address") or {}

    return {
        "event_id": event.get("id"),
        "event_name": event.get("name"),
        "event_type": event.get("type"),
        "event_url": event.get("url"),
        "local_date": start.get("localDate"),
        "local_time": start.get("localTime"),
        "date_time_utc": start.get("dateTime"),
        "timezone": dates.get("timezone"),
        "status_code": status.get("code"),
        "public_sale_start_utc": sales_public.get("startDateTime"),
        "public_sale_end_utc": sales_public.get("endDateTime"),
        "public_sale_start_tbd": sales_public.get("startTBD"),
        "price_type": price_range.get("type"),
        "price_currency": price_range.get("currency"),
        "price_min": price_range.get("min"),
        "price_max": price_range.get("max"),
        "venue_id": venue.get("id"),
        "venue_name": venue.get("name"),
        "venue_city": city.get("name"),
        "venue_state_code": state.get("stateCode"),
        "venue_country_code": country.get("countryCode"),
        "venue_postal_code": venue.get("postalCode"),
        "venue_address": address.get("line1"),
        "venue_latitude": location.get("latitude"),
        "venue_longitude": location.get("longitude"),
        "attraction_ids": "|".join(str(a.get("id")) for a in attractions if a.get("id")),
        "attraction_names": "|".join(a.get("name", "") for a in attractions if a.get("name")),
        "segment": segment.get("name"),
        "genre": genre.get("name"),
        "subgenre": subgenre.get("name"),
        "last_seen_ts_utc": run_ts.isoformat(timespec="seconds"),
    }


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse to one row per event_id (last occurrence wins).

    Pagination can shift between API calls within a run, so the same event
    can appear twice in a sweep; MERGE requires a unique key on the source.
    """

    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("event_id"):
            by_id[row["event_id"]] = row
    return list(by_id.values())


def upsert_to_silver(rows: list[dict[str, Any]]) -> int:
    """MERGE flattened rows into BigQuery tm_events; return rows affected.

    Upsert semantics, keyed on event_id:
      - existing id -> UPDATE the row in place (fresh status/prices/dates,
        last_seen_ts_utc bumped); first_seen_ts_utc is left untouched.
      - new id      -> INSERT, with first_seen_ts_utc = this run's timestamp.

    Rows are loaded into a staging table (truncated each run) so the MERGE is
    a single atomic statement — re-running it with the same data is a no-op,
    which keeps the pipeline idempotent.
    """

    project_id = os.environ["GCP_PROJECT"]
    dataset = os.environ["BQ_DATASET"]
    silver = f"{project_id}.{dataset}.tm_events"
    staging = f"{project_id}.{dataset}.tm_events_staging"

    client = bigquery.Client(project=project_id)

    columns_ddl = ",\n  ".join(f"{name} {bq_type}" for name, bq_type in STAGING_SCHEMA)
    client.query(
        f"CREATE TABLE IF NOT EXISTS `{silver}` (\n"
        f"  {columns_ddl},\n"
        f"  first_seen_ts_utc TIMESTAMP\n"
        f") CLUSTER BY venue_state_code"
    ).result()

    schema = [
        bigquery.SchemaField(name, _LEGACY_TYPES.get(bq_type, bq_type))
        for name, bq_type in STAGING_SCHEMA
    ]
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(rows, staging, job_config=job_config).result()

    staging_cols = [name for name, _ in STAGING_SCHEMA]
    update_cols = [c for c in staging_cols if c != "event_id"]
    merge_sql = (
        f"MERGE `{silver}` T\n"
        f"USING `{staging}` S\n"
        f"ON T.event_id = S.event_id\n"
        f"WHEN MATCHED THEN UPDATE SET\n  "
        + ",\n  ".join(f"T.{c} = S.{c}" for c in update_cols)
        + f"\nWHEN NOT MATCHED THEN INSERT ({', '.join(staging_cols)}, first_seen_ts_utc)\n"
        f"VALUES ({', '.join(f'S.{c}' for c in staging_cols)}, S.last_seen_ts_utc)"
    )
    merge_job = client.query(merge_sql)
    merge_job.result()
    return int(merge_job.num_dml_affected_rows or 0)


def export_to_processed(run_ts: datetime) -> str:
    """Export the deduplicated tm_events table to the processed bucket.

    Writes Parquet under processed/ticketmaster/dt=<date>/ after each merge,
    so the bucket always holds a clean file copy of the silver table: bronze
    keeps raw snapshots WITH duplicates, this layer is deduplicated. Re-runs
    on the same day overwrite that day's export.
    """

    project_id = os.environ["GCP_PROJECT"]
    dataset = os.environ["BQ_DATASET"]
    bucket = os.environ["GCS_PROCESSED_BUCKET"]
    dt = run_ts.strftime("%Y-%m-%d")
    uri = f"gs://{bucket}/ticketmaster/dt={dt}/tm_events_*.parquet"

    client = bigquery.Client(project=project_id)
    client.query(
        f"EXPORT DATA OPTIONS (uri='{uri}', format='PARQUET', overwrite=true) AS\n"
        f"SELECT * FROM `{project_id}.{dataset}.tm_events`"
    ).result()
    return uri


@functions_framework.http
def run(request):  # noqa: ARG001 — Scheduler sends an empty POST body
    """HTTP entry point invoked by Cloud Scheduler.

    Sweeps every configured state, uploading each state's raw capture as soon
    as it is fetched (keeps memory flat and partial progress durable). Only
    raises — returning a 5xx so Cloud Scheduler retries — when ALL states
    fail; isolated state failures are reported in the summary instead, since
    a full retry would re-spend the whole call budget.
    """

    run_ts = datetime.now(timezone.utc)
    max_calls = int(os.environ.get("MAX_CALLS_PER_RUN", "780"))

    states = state_codes()
    total_calls = 0
    total_events = 0
    states_uploaded = 0
    failed_states: dict[str, str] = {}
    skipped_states: list[str] = []
    silver_rows: list[dict[str, Any]] = []

    for state in states:
        if total_calls >= max_calls:
            skipped_states.append(state)
            continue
        try:
            events, calls = search_state_events(state, run_ts, max_calls - total_calls)
            total_calls += calls
            total_events += len(events)
            if events:
                upload_raw(state, events, run_ts)
                states_uploaded += 1
                silver_rows.extend(flatten_event(e, run_ts) for e in events)
        except Exception as exc:  # isolate per-state failures
            failed_states[state] = f"{type(exc).__name__}: {exc}"
            print(f"[{state}] FAILED: {failed_states[state]}")

    # Upsert into the deduplicated silver table. A failure here is reported,
    # not raised: the raw snapshots already landed, the MERGE is idempotent,
    # and the next scheduled run refreshes the same rows anyway — whereas a
    # Scheduler retry would re-spend the whole API call budget.
    silver: dict[str, Any] = {
        "rows_in": 0,
        "rows_upserted": None,
        "processed_export_uri": None,
        "error": None,
    }
    if silver_rows:
        unique_rows = dedupe_rows(silver_rows)
        silver["rows_in"] = len(unique_rows)
        try:
            silver["rows_upserted"] = upsert_to_silver(unique_rows)
            silver["processed_export_uri"] = export_to_processed(run_ts)
        except Exception as exc:
            silver["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[silver] FAILED: {silver['error']}")

    summary: dict[str, Any] = {
        "run_ts_utc": run_ts.isoformat(timespec="seconds"),
        "states_swept": len(states) - len(skipped_states),
        "states_uploaded": states_uploaded,
        "events_fetched": total_events,
        "api_calls": total_calls,
        "failed_states": failed_states,
        "skipped_states": skipped_states,
        "silver": silver,
    }
    print(json.dumps(summary))  # lands in Cloud Logging for run history

    if failed_states or skipped_states or silver["error"]:
        # stderr lines get severity ERROR in Cloud Logging — that's what the
        # monitoring alert policy (terraform/monitoring.tf) emails on.
        # Without this, partial failures hide as INFO inside a 200 response.
        print(json.dumps(summary), file=sys.stderr)

    if states and len(failed_states) == len(states):
        raise RuntimeError(f"All {len(states)} states failed: {failed_states}")

    return summary
