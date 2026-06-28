#!/usr/bin/env python3
"""Google Trends bronze -> silver: build ``fact_trends_daily`` (artist x dma x **day**).

The sibling ``trends_to_silver.py`` loads the ``ibr_DMA`` **snapshot** (one cross-DMA
cross-section per *collection day*). This loads the **``interest_over_time`` per-DMA
daily series** (``dma`` unit kind, ``resolution=dma_series``) — the real **daily interest
trajectory** the locked schema (``docs/data-model.md``) intends: for each (artist, DMA),
~270 days of daily 0-100 interest, **backfillable** in one pull. ~9,200 of these were
already collected in the 2026-06-14..16 deep backfill and sat unused in bronze.

Why a separate table (additive, not a replacement of ``fact_trends``):
  * The ``iot`` series is **deep** (back ~9 months) but currently **static** (the deep
    backfill stopped ~2026-06-16); the ``ibr`` snapshot is **shallow** but **daily-fresh**.
  * Their 0-100 values use **different normalizations** — ``iot`` is comparable across
    *time within one (artist, DMA)*; ``ibr`` is comparable *across DMAs at one moment*.
  Mixing them in one column would be wrong, so the daily trajectory lands here and the
  working ``fact_trends`` / gold / model are left untouched (rewiring is a follow-on).

> ⚠️ ``interest`` is **per-pull normalized 0-100** — comparable across time for one artist
> in one DMA, never across artists or DMAs (see docs/data-model.md).

Keys are the same deterministic surrogates as the rest of silver (``common/keys.py``), so
this joins to the dims; idempotent staging+MERGE on ``trends_snapshot_id``. No LLM.

Run (repo root; BigQuery/GCS authed via ADC):
    python pipeline/silver/trends_series_to_silver.py --dry-run        # bronze -> count, no write
    python pipeline/silver/trends_series_to_silver.py                  # full bronze -> fact_trends_daily
    python pipeline/silver/trends_series_to_silver.py --from-fixtures <dir>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from common.keys import artist_id, snapshot_id  # noqa: E402

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"
SOURCE = "google_trends"
FACT_TABLE = "fact_trends_daily"
GRANULARITY = "daily"  # iot per-DMA = a daily interest_over_time trajectory

_DT_RE = re.compile(r"dt=([0-9]{4}-[0-9]{2}-[0-9]{2})")

# (column, BigQuery type) in table order — same grain/columns as fact_trends.
STAGING_SCHEMA: list[tuple[str, str]] = [
    ("trends_snapshot_id", "STRING"),
    ("artist_id", "INT64"),
    ("dma_code", "STRING"),
    ("snapshot_date", "DATE"),
    ("interest", "INT64"),
    ("granularity", "STRING"),
    ("is_partial", "BOOL"),
    ("category", "STRING"),
    ("load_ts_utc", "TIMESTAMP"),
]
_LEGACY_TYPES = {"INT64": "INTEGER", "BOOL": "BOOLEAN"}


# ---------------------------------------------------------------------------
# Pure transform (unit-tested offline; no network, no BigQuery)
# ---------------------------------------------------------------------------

def dt_from_path(path: str) -> str | None:
    m = _DT_RE.search(str(path))
    return m.group(1) if m else None


def _to_int(value) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def bare_dma(geo_code: str | None) -> str:
    """``US-CA-807`` -> ``807`` (the bare Nielsen DMA that dim_geo / venues key on)."""
    return (geo_code or "").strip().split("-")[-1].strip()


def series_payload_to_rows(payload: dict, capture_dt: str, load_ts: str) -> list[dict]:
    """Flatten one ``iot`` per-DMA daily payload into fact_trends_daily rows.

    Returns ``[]`` for any non-(interest_over_time, dma_series) payload, so a directory of
    mixed captures (national / snapshot / hourly / dma) can be passed straight through.
    ``_capture_dt`` rides along for cross-capture dedup and is dropped before load.
    """
    if payload.get("endpoint") != "interest_over_time" or payload.get("resolution") != "dma_series":
        return []
    query = payload.get("query")
    aid = artist_id(payload.get("artist"))
    dma = bare_dma(payload.get("geo_code"))
    if not dma:
        return []
    rows = []
    for rec in payload.get("records", []):
        snapshot_date = (rec.get("date") or "")[:10]
        interest = _to_int(rec.get(query))
        if not snapshot_date or interest is None:
            continue
        rows.append({
            "trends_snapshot_id": snapshot_id("trends", aid, dma, snapshot_date),
            "artist_id": aid,
            "dma_code": dma,
            "snapshot_date": snapshot_date,
            "interest": interest,
            "granularity": GRANULARITY,
            "is_partial": bool(rec.get("isPartial", False)),
            "category": payload.get("category") or "",
            "load_ts_utc": load_ts,
            "_capture_dt": capture_dt,
        })
    return rows


def rows_from_payloads(payloads: list[tuple[str, dict]], load_ts: str) -> list[dict]:
    """Flatten ``(capture_dt, payload)`` pairs into all fact_trends_daily rows."""
    rows: list[dict] = []
    for capture_dt, payload in payloads:
        rows.extend(series_payload_to_rows(payload, capture_dt, load_ts))
    return rows


def dedupe_latest(rows: list[dict]) -> list[dict]:
    """One row per trends_snapshot_id, keeping the **latest capture** deterministically.

    The same (artist, DMA, day) can appear in multiple overlapping captures; the most
    recent pull has the most up-to-date normalization, so it wins (ties: keep first seen).
    The ``_capture_dt`` helper key is stripped from the result.
    """
    best: dict[str, dict] = {}
    for r in rows:
        k = r["trends_snapshot_id"]
        cur = best.get(k)
        if cur is None or r["_capture_dt"] > cur["_capture_dt"]:
            best[k] = r
    return [{k: v for k, v in r.items() if k != "_capture_dt"} for r in best.values()]


# ---------------------------------------------------------------------------
# I/O (kept thin so the transform above stays unit-testable)
# ---------------------------------------------------------------------------

def read_fixture_dir(base: str) -> list[tuple[str, dict]]:
    """Read committed bronze fixtures: ``<base>/dt=YYYY-MM-DD/*.json``."""
    out = []
    for path in sorted(Path(base).glob("dt=*/*.json")):
        out.append((dt_from_path(path), json.loads(path.read_text(encoding="utf-8"))))
    return out


def read_bronze(project: str, start: str | None, end: str | None,
                workers: int = 16) -> list[tuple[str, dict]]:
    """Parallel-read the bronze ``iot`` per-DMA daily captures (``*_iot_US-*.json``).

    ~10k small JSON blobs — a threaded ``storage`` download is ~minutes vs hours of
    sequential ``gsutil cat``. Blobs are listed + sorted deterministically (dt, name).
    """
    from google.cloud import storage

    client = storage.Client(project=project)
    bucket = f"{project}-raw"
    pairs = []
    for blob in client.list_blobs(bucket, prefix=f"{SOURCE}/"):
        name = blob.name
        if "_iot_US-" not in name or not name.endswith(".json"):
            continue  # national iot_US_ has an underscore (not dash) after US -> excluded
        dt = dt_from_path(name)
        if dt is None or (start and dt < start) or (end and dt > end):
            continue
        pairs.append((dt, blob))
    pairs.sort(key=lambda p: (p[0], p[1].name))

    def fetch(item: tuple[str, object]) -> tuple[str, dict]:
        dt, blob = item
        return dt, json.loads(blob.download_as_text())

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fetch, pairs))


def upsert_to_silver(rows: list[dict], project: str, dataset: str) -> int:
    """MERGE fact_trends_daily rows on trends_snapshot_id; return rows affected.

    Mirrors trends_to_silver: load into a truncated staging table, then one atomic MERGE
    — idempotent, so re-running the same captures is a no-op.
    """
    from google.cloud import bigquery  # lazy: keeps the transform import offline-safe

    fq = f"{project}.{dataset}.{FACT_TABLE}"
    staging = f"{project}.{dataset}.{FACT_TABLE}_staging"
    client = bigquery.Client(project=project)

    cols_ddl = ",\n  ".join(f"{name} {bq_type}" for name, bq_type in STAGING_SCHEMA)
    client.query(
        f"CREATE TABLE IF NOT EXISTS `{fq}` (\n  {cols_ddl}\n)\n"
        f"PARTITION BY snapshot_date CLUSTER BY artist_id"
    ).result()

    schema = [bigquery.SchemaField(name, _LEGACY_TYPES.get(bq_type, bq_type))
              for name, bq_type in STAGING_SCHEMA]
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(rows, staging, job_config=job_config).result()

    cols = [name for name, _ in STAGING_SCHEMA]
    update_cols = [c for c in cols if c != "trends_snapshot_id"]
    merge_sql = (
        f"MERGE `{fq}` T\nUSING `{staging}` S\n"
        f"ON T.trends_snapshot_id = S.trends_snapshot_id\n"
        f"WHEN MATCHED THEN UPDATE SET\n  "
        + ",\n  ".join(f"T.{c} = S.{c}" for c in update_cols)
        + f"\nWHEN NOT MATCHED THEN INSERT ({', '.join(cols)})\n"
        f"VALUES ({', '.join(f'S.{c}' for c in cols)})"
    )
    job = client.query(merge_sql)
    job.result()
    return int(job.num_dml_affected_rows or 0)


def summarize(rows: list[dict]) -> dict:
    return {
        "rows": len(rows),
        "artists": len({r["artist_id"] for r in rows}),
        "dmas": len({r["dma_code"] for r in rows}),
        "snapshot_dates": len({r["snapshot_date"] for r in rows}),
        "date_min": min((r["snapshot_date"] for r in rows), default=None),
        "date_max": max((r["snapshot_date"] for r in rows), default=None),
        "interest_min": min((r["interest"] for r in rows), default=None),
        "interest_max": max((r["interest"] for r in rows), default=None),
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
        payloads = read_bronze(args.project, args.start_date, args.end_date)

    rows = dedupe_latest(rows_from_payloads(payloads, load_ts))
    print(f"[trends_series_to_silver] {summarize(rows)}")

    if not rows:
        print("[trends_series_to_silver] no dma_series rows found", file=sys.stderr)
        return 1
    if args.dry_run:
        print("[trends_series_to_silver] dry-run: nothing written")
        return 0

    affected = upsert_to_silver(rows, args.project, args.dataset)
    print(f"[trends_series_to_silver] merged {affected} rows into "
          f"{args.project}.{args.dataset}.{FACT_TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
