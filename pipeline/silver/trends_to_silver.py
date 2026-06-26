#!/usr/bin/env python3
"""Google Trends bronze -> silver: build ``fact_trends`` (artist x dma x snapshot_date).

Reads the raw Trends **DMA-snapshot** captures (``interest_by_region`` / ``ibr_DMA``)
the collector lands in the bronze bucket and flattens each into one row per
**(artist, dma, snapshot_date)** — the locked ``fact_trends`` grain in
``docs/data-model.md``. Each ``ibr_DMA`` file is one artist's interest distributed
across ~210 Nielsen DMAs, captured on its ``dt=`` collection day; that day is the
``snapshot_date``, ``geoCode`` is the ``dma_code``, and the interest value lives under
the ``query`` column pytrends returned.

The national ``iot_US`` series is a different grain (artist x day, no DMA) and is not
part of the locked ``fact_trends``; it stays in bronze for a future national-interest
feature.

Keys are deterministic surrogates from ``common/keys.py`` (``artist_id`` from the
normalized artist name; ``trends_snapshot_id`` from the business key) so this transform
builds independently of the dimensions (A3) yet joins to them. Idempotent: re-loading
the same snapshot MERGEs in place on ``trends_snapshot_id``. No LLM at runtime.

> ⚠️ ``interest`` is **per-pull normalized 0-100** — comparable across time for one
> artist in one DMA, never across artists or DMAs (see docs/data-model.md).

Run (repo root; ``gsutil``/BigQuery authed via ADC):
    python pipeline/silver/trends_to_silver.py --dry-run            # bronze -> count, no write
    python pipeline/silver/trends_to_silver.py                      # full bronze -> fact_trends
    python pipeline/silver/trends_to_silver.py --from-fixtures tests/fixtures/seed/google_trends
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from common.keys import artist_id, snapshot_id  # noqa: E402

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"
SOURCE = "google_trends"
FACT_TABLE = "fact_trends"
GRANULARITY = "snapshot"  # ibr_DMA = a DMA distribution captured per collection day

_DT_RE = re.compile(r"dt=([0-9]{4}-[0-9]{2}-[0-9]{2})")

# (column, BigQuery type) in table order — matches docs/data-model.md fact_trends,
# plus load_ts_utc (audit only; not an analytical column).
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


def snapshot_payload_to_rows(payload: dict, snapshot_date: str, load_ts: str) -> list[dict]:
    """Flatten one ``ibr_DMA`` payload into fact_trends rows (one per DMA).

    Returns ``[]`` for any non-DMA-snapshot payload (e.g. the national ``iot_US``
    series), so a directory of mixed captures can be passed straight through.
    """
    if payload.get("endpoint") != "interest_by_region" or payload.get("resolution") != "DMA":
        return []
    artist = payload.get("artist")
    query = payload.get("query")
    aid = artist_id(artist)
    rows = []
    for rec in payload.get("records", []):
        dma = (rec.get("geoCode") or "").strip()
        interest = _to_int(rec.get(query))
        if not dma or interest is None:
            continue
        rows.append({
            "trends_snapshot_id": snapshot_id("trends", aid, dma, snapshot_date),
            "artist_id": aid,
            "dma_code": dma,
            "snapshot_date": snapshot_date,
            "interest": interest,
            "granularity": GRANULARITY,
            "is_partial": False,  # interest_by_region carries no isPartial flag
            "category": payload.get("category") or "",
            "load_ts_utc": load_ts,
        })
    return rows


def rows_from_payloads(payloads: list[tuple[str, dict]], load_ts: str) -> list[dict]:
    """Flatten ``(snapshot_date, payload)`` pairs into all fact_trends rows."""
    rows: list[dict] = []
    for snapshot_date, payload in payloads:
        rows.extend(snapshot_payload_to_rows(payload, snapshot_date, load_ts))
    return rows


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """One row per trends_snapshot_id (last wins) so the MERGE key stays unique."""
    by_id = {r["trends_snapshot_id"]: r for r in rows}
    return list(by_id.values())


# ---------------------------------------------------------------------------
# I/O (kept thin so the transform above stays unit-testable)
# ---------------------------------------------------------------------------

def read_fixture_dir(base: str) -> list[tuple[str, dict]]:
    """Read committed bronze fixtures: ``<base>/dt=YYYY-MM-DD/*.json``."""
    out = []
    for path in sorted(Path(base).glob("dt=*/*.json")):
        out.append((dt_from_path(path), json.loads(path.read_text(encoding="utf-8"))))
    return out


def list_bronze_snapshots(project: str, start: str | None, end: str | None) -> list[str]:
    """List the bronze ``ibr_DMA`` capture URIs, optionally bounded by ``dt=``."""
    pattern = f"gs://{project}-raw/{SOURCE}/dt=*/{SOURCE}_ibr_DMA_*.json"
    proc = subprocess.run(["gsutil", "ls", pattern],
                          capture_output=True, text=True, check=True)
    kept = []
    for uri in (u.strip() for u in proc.stdout.splitlines() if u.strip()):
        dt = dt_from_path(uri)
        if dt is None or (start and dt < start) or (end and dt > end):
            continue
        kept.append(uri)
    return sorted(kept)


def read_bronze(project: str, start: str | None, end: str | None) -> list[tuple[str, dict]]:
    out = []
    for uri in list_bronze_snapshots(project, start, end):
        proc = subprocess.run(["gsutil", "cat", uri],
                              capture_output=True, text=True, check=True)
        out.append((dt_from_path(uri), json.loads(proc.stdout)))
    return out


def upsert_to_silver(rows: list[dict], project: str, dataset: str) -> int:
    """MERGE fact_trends rows into BigQuery on trends_snapshot_id; return rows affected.

    Mirrors the tm_events upsert: load into a truncated staging table, then a single
    atomic MERGE — idempotent, so re-running the same snapshots is a no-op.
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
    parser.add_argument("--start-date", default=None, help="earliest dt= (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="latest dt= (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="transform + summarize but write nothing to BigQuery")
    args = parser.parse_args()

    load_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.from_fixtures:
        payloads = read_fixture_dir(args.from_fixtures)
    else:
        payloads = read_bronze(args.project, args.start_date, args.end_date)

    rows = dedupe_rows(rows_from_payloads(payloads, load_ts))
    summary = summarize(rows)
    print(f"[trends_to_silver] {summary}")

    if not rows:
        print("[trends_to_silver] no DMA-snapshot rows found", file=sys.stderr)
        return 1
    if args.dry_run:
        print("[trends_to_silver] dry-run: nothing written")
        return 0

    affected = upsert_to_silver(rows, args.project, args.dataset)
    print(f"[trends_to_silver] merged {affected} rows into "
          f"{args.project}.{args.dataset}.{FACT_TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
