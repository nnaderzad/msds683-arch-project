#!/usr/bin/env python3
"""Ticketmaster bronze -> silver: build ``tm_observations`` (event x **observed** day).

This is the **honest** price-history source. The cloud function MERGE-upserts current-state
``tm_events`` (never deletes) and exports it to processed parquet, so the old
``fact_ticketmaster`` (which read that parquet) carried every event's **last-known price
forward** into every later day -- manufacturing a daily series the sweep never observed
(``eda/diagnose_price_gaps.py`` proved 0 interior / 0 trailing gaps, structurally impossible
without forward-fill).

Here we go back to **bronze**: the raw per-state JSON the sweep actually returned. The
scheduler runs every 4 hours -> **up to 6 captures per (event, day)**; each capture is one
stamped file. Bronze stays 100% raw; this loader is the only place that **aggregates** the
day's captures into ONE honest row per (event, snapshot_date). A row exists **only for days
the event was actually observed**; price is **as observed** (NULL if observed without a
price). **No forward-fill here** -- gap-filling for the demo is a separate, explicitly
labeled gold table.

Canonical within-day aggregation rule (mirrored incrementally by the cloud function's
``append_observations``):
  * **presence** = union across the day's captures (observed if *any* run saw it -- robust
    to a partial / budget-capped run; "latest" is not reliably the most complete).
  * **price** = priced-if-any, **latest priced value** (the most recent capture that
    actually carried a price wins; NULL only if no capture that day had one).
  * provenance: ``n_captures`` (1-6, how many runs saw it) and ``price_disagreed`` (the
    day's captures conflicted on price / presence).

Keys are the same deterministic surrogates as the rest of silver (``common/keys.py``); the
dbt model ``fact_ticketmaster`` reads this table and adds typing / ``days_to_show`` /
``tm_snapshot_id``. Idempotent staging+MERGE on ``tm_obs_id``. No LLM at runtime.

> The canonical event flattener is ``cloud_functions/ticketmaster_daily/main.py``
> (``flatten_event`` / ``first_price_range``); that dir ships in isolation and can't import
> this repo, so the **minimal price/status subset** is replicated below.

Run (repo root; BigQuery/GCS authed via ADC):
    python pipeline/silver/tm_observations_to_silver.py --dry-run          # bronze -> count, no write
    python pipeline/silver/tm_observations_to_silver.py                    # full bronze -> tm_observations
    python pipeline/silver/tm_observations_to_silver.py --start-date 2026-06-27   # chunk by capture day
    python pipeline/silver/tm_observations_to_silver.py --from-fixtures <dir>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from common.keys import snapshot_id  # noqa: E402

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"
SOURCE = "ticketmaster"
FACT_TABLE = "tm_observations"

_DT_RE = re.compile(r"dt=([0-9]{4}-[0-9]{2}-[0-9]{2})")
_STAMP_RE = re.compile(r"([0-9]{8}T[0-9]{6}Z)")  # run stamp in the filename -> capture order

# (column, BigQuery type) in table order.
STAGING_SCHEMA: list[tuple[str, str]] = [
    ("tm_obs_id", "STRING"),
    ("event_id", "STRING"),
    ("snapshot_date", "DATE"),
    ("local_date", "DATE"),
    ("status_code", "STRING"),
    ("price_type", "STRING"),
    ("price_currency", "STRING"),
    ("price_min", "FLOAT64"),
    ("price_max", "FLOAT64"),
    ("public_sale_start_utc", "TIMESTAMP"),
    ("public_sale_end_utc", "TIMESTAMP"),
    ("n_captures", "INT64"),
    ("price_disagreed", "BOOL"),
    ("load_ts_utc", "TIMESTAMP"),
]
_LEGACY_TYPES = {"INT64": "INTEGER", "BOOL": "BOOLEAN", "FLOAT64": "FLOAT"}


# ---------------------------------------------------------------------------
# Pure transform (unit-tested offline; no network, no BigQuery)
# ---------------------------------------------------------------------------

def dt_from_path(path: str) -> str | None:
    m = _DT_RE.search(str(path))
    return m.group(1) if m else None


def stamp_from_path(path: str) -> str | None:
    m = _STAMP_RE.search(str(path))
    return m.group(1) if m else None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_price_range(event: dict) -> dict:
    """Pick one price range from the event, preferring standard prices.

    Mirrors cloud_functions/ticketmaster_daily/main.py:first_price_range (the canonical one).
    """
    ranges = event.get("priceRanges") or []
    for item in ranges:
        if item.get("type") == "standard":
            return item
    return ranges[0] if ranges else {}


def event_to_obs_row(event: dict, snapshot_date: str, capture_stamp: str | None) -> dict | None:
    """One raw Ticketmaster event -> one per-capture observation row (price-history subset).

    Returns ``None`` for an event with no id. ``_capture_stamp`` rides along so the day's
    captures can be ordered for the latest-priced rule; it is dropped in ``aggregate_day``.
    """
    event_id = event.get("id")
    if not event_id:
        return None
    dates = event.get("dates") or {}
    start = dates.get("start") or {}
    status = dates.get("status") or {}
    sales_public = (event.get("sales") or {}).get("public") or {}
    price = first_price_range(event)
    return {
        "event_id": event_id,
        "snapshot_date": snapshot_date,
        "local_date": start.get("localDate"),
        "status_code": status.get("code"),
        "price_type": price.get("type"),
        "price_currency": price.get("currency"),
        "price_min": _to_float(price.get("min")),
        "price_max": _to_float(price.get("max")),
        "public_sale_start_utc": sales_public.get("startDateTime"),
        "public_sale_end_utc": sales_public.get("endDateTime"),
        "_capture_stamp": capture_stamp or "",
    }


def aggregate_day(capture_rows: list[dict], load_ts: str) -> dict:
    """Collapse one (event_id, snapshot_date)'s captures into ONE honest row (the rule).

    A capture = one run's file (one ``_capture_stamp``). Bronze raw files repeat an event
    across pages, so first collapse pagination dups **within** each file (preferring a priced
    occurrence — identical within a file in practice) to one row per file. Then:
    ``n_captures`` = how many runs/files that day saw it (~<=6 recent, 1 on early single-file
    days); price = priced-if-any with the **latest priced capture** winning; non-price fields
    = latest capture overall; ``price_disagreed`` = the day's captures conflicted.
    """
    by_stamp: dict[str, dict] = {}
    for r in capture_rows:
        stamp = r["_capture_stamp"]
        cur = by_stamp.get(stamp)
        if cur is None or (cur["price_min"] is None and r["price_min"] is not None):
            by_stamp[stamp] = r
    ordered = sorted(by_stamp.values(), key=lambda r: r["_capture_stamp"])
    latest = ordered[-1]
    event_id, snapshot_date = latest["event_id"], latest["snapshot_date"]
    n_captures = len(ordered)

    priced = [r for r in ordered if r["price_min"] is not None]
    latest_priced = priced[-1] if priced else None
    distinct_prices = {(r["price_min"], r["price_max"]) for r in priced}
    # Disagree if priced captures differ, or some captures priced while others did not.
    price_disagreed = len(distinct_prices) > 1 or (0 < len(priced) < n_captures)

    return {
        "tm_obs_id": snapshot_id("tm", event_id, snapshot_date),
        "event_id": event_id,
        "snapshot_date": snapshot_date,
        "local_date": latest["local_date"],
        "status_code": latest["status_code"],
        "price_type": latest_priced["price_type"] if latest_priced else None,
        "price_currency": latest_priced["price_currency"] if latest_priced else None,
        "price_min": latest_priced["price_min"] if latest_priced else None,
        "price_max": latest_priced["price_max"] if latest_priced else None,
        "public_sale_start_utc": latest["public_sale_start_utc"],
        "public_sale_end_utc": latest["public_sale_end_utc"],
        "n_captures": n_captures,
        "price_disagreed": price_disagreed,
        "load_ts_utc": load_ts,
    }


def rows_from_payloads(payloads, load_ts: str) -> list[dict]:
    """Flatten ``(dt, capture_stamp, events_list)`` triples into aggregated daily rows.

    Groups every capture by (event_id, snapshot_date) then applies ``aggregate_day``.
    Accepts an iterable so the driver can stream bronze (dropping each raw array promptly).
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for dt, capture_stamp, events in payloads:
        if not isinstance(events, list):
            continue
        for event in events:
            row = event_to_obs_row(event, dt, capture_stamp)
            if row is not None:
                groups[(row["event_id"], row["snapshot_date"])].append(row)
    return [aggregate_day(rows, load_ts) for rows in groups.values()]


# ---------------------------------------------------------------------------
# I/O (kept thin so the transform above stays unit-testable)
# ---------------------------------------------------------------------------

def read_fixture_dir(base: str):
    """Read committed bronze fixtures: ``<base>/dt=YYYY-MM-DD/*.json`` (arrays of events)."""
    for path in sorted(Path(base).glob("dt=*/*.json")):
        yield dt_from_path(path), stamp_from_path(path), json.loads(path.read_text(encoding="utf-8"))


def iter_bronze(project: str, start: str | None, end: str | None, workers: int = 16):
    """Stream the bronze Ticketmaster captures (``ticketmaster/dt=*/ticketmaster_*.json``).

    ~5.3k JSON-array blobs -- a threaded ``storage`` download is minutes vs hours of
    sequential ``gsutil cat``. Yields ``(dt, capture_stamp, events_list)`` as each completes,
    so the driver can fold captures into per-day groups without holding every raw array.
    """
    from google.cloud import storage

    client = storage.Client(project=project)
    bucket = f"{project}-raw"
    pairs = []
    for blob in client.list_blobs(bucket, prefix=f"{SOURCE}/"):
        name = blob.name
        if not name.endswith(".json"):
            continue
        dt = dt_from_path(name)
        if dt is None or (start and dt < start) or (end and dt > end):
            continue
        pairs.append((dt, stamp_from_path(name), blob))
    pairs.sort(key=lambda p: (p[0], p[2].name))

    def fetch(item):
        dt, stamp, blob = item
        return dt, stamp, json.loads(blob.download_as_text())

    with ThreadPoolExecutor(max_workers=workers) as ex:
        yield from ex.map(fetch, pairs)


def upsert_to_silver(rows: list[dict], project: str, dataset: str) -> int:
    """MERGE tm_observations rows on tm_obs_id; return rows affected.

    Mirrors the trends loaders: load into a truncated staging table, then one atomic MERGE
    -- idempotent, so re-running the same captures is a no-op.
    """
    from google.cloud import bigquery  # lazy: keeps the transform import offline-safe

    fq = f"{project}.{dataset}.{FACT_TABLE}"
    staging = f"{project}.{dataset}.{FACT_TABLE}_staging"
    client = bigquery.Client(project=project)

    cols_ddl = ",\n  ".join(f"{name} {bq_type}" for name, bq_type in STAGING_SCHEMA)
    client.query(
        f"CREATE TABLE IF NOT EXISTS `{fq}` (\n  {cols_ddl}\n)\n"
        f"PARTITION BY snapshot_date CLUSTER BY event_id"
    ).result()

    schema = [bigquery.SchemaField(name, _LEGACY_TYPES.get(bq_type, bq_type))
              for name, bq_type in STAGING_SCHEMA]
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(rows, staging, job_config=job_config).result()

    cols = [name for name, _ in STAGING_SCHEMA]
    update_cols = [c for c in cols if c != "tm_obs_id"]
    merge_sql = (
        f"MERGE `{fq}` T\nUSING `{staging}` S\n"
        f"ON T.tm_obs_id = S.tm_obs_id\n"
        f"WHEN MATCHED THEN UPDATE SET\n  "
        + ",\n  ".join(f"T.{c} = S.{c}" for c in update_cols)
        + f"\nWHEN NOT MATCHED THEN INSERT ({', '.join(cols)})\n"
        f"VALUES ({', '.join(f'S.{c}' for c in cols)})"
    )
    job = client.query(merge_sql)
    job.result()
    return int(job.num_dml_affected_rows or 0)


def summarize(rows: list[dict]) -> dict:
    priced = [r for r in rows if r["price_min"] is not None]
    captures = [r["n_captures"] for r in rows]
    return {
        "rows": len(rows),
        "events": len({r["event_id"] for r in rows}),
        "snapshot_dates": len({r["snapshot_date"] for r in rows}),
        "date_min": min((r["snapshot_date"] for r in rows), default=None),
        "date_max": max((r["snapshot_date"] for r in rows), default=None),
        "priced_rows": len(priced),
        "disagreed_rows": sum(1 for r in rows if r["price_disagreed"]),
        "n_captures_min": min(captures, default=None),
        "n_captures_max": max(captures, default=None),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--from-fixtures", default=None,
                        help="read committed bronze fixtures from this dir instead of GCS")
    parser.add_argument("--start-date", default=None, help="earliest capture dt= (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="latest capture dt= (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="transform + summarize but write nothing to BigQuery")
    args = parser.parse_args()

    load_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.from_fixtures:
        payloads = read_fixture_dir(args.from_fixtures)
    else:
        payloads = iter_bronze(args.project, args.start_date, args.end_date)

    rows = rows_from_payloads(payloads, load_ts)
    print(f"[tm_observations_to_silver] {summarize(rows)}")

    if not rows:
        print("[tm_observations_to_silver] no observation rows found", file=sys.stderr)
        return 1
    if args.dry_run:
        print("[tm_observations_to_silver] dry-run: nothing written")
        return 0

    affected = upsert_to_silver(rows, args.project, args.dataset)
    print(f"[tm_observations_to_silver] merged {affected} rows into "
          f"{args.project}.{args.dataset}.{FACT_TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
