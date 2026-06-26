#!/usr/bin/env python3
"""YouTube bronze -> silver: build ``fact_youtube`` (artist x snapshot_date).

Reads the raw daily YouTube captures the collector lands in the bronze bucket and
flattens each artist record into one row per **(artist, snapshot_date)** — the locked
``fact_youtube`` grain in ``docs/data-model.md``. Each bronze file is one collection
day's snapshot of the roster; that day's ``dt=`` is the ``snapshot_date`` and each
``records[]`` entry is one artist.

Only the per-snapshot **measures** land here (subscribers / views / video counts);
the channel ids/titles are artist attributes and live on ``dim_artist`` (A3), per the
schema's change #7 — so they're intentionally dropped from the fact.

Keys are deterministic surrogates from ``common/keys.py`` (``artist_id`` from the
normalized artist name — the same join key A1 used), so this builds independently of the
dimensions yet joins to them. Idempotent: re-loading a day MERGEs in place on
``youtube_snapshot_id``. No LLM at runtime.

Run (repo root; ``gsutil``/BigQuery authed via ADC):
    python pipeline/silver/youtube_to_silver.py --dry-run
    python pipeline/silver/youtube_to_silver.py
    python pipeline/silver/youtube_to_silver.py --from-fixtures tests/fixtures/seed/youtube
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
SOURCE = "youtube"
FACT_TABLE = "fact_youtube"

_DT_RE = re.compile(r"dt=([0-9]{4}-[0-9]{2}-[0-9]{2})")

# (column, BigQuery type) in table order — matches docs/data-model.md fact_youtube,
# plus load_ts_utc (audit only). The numeric measures are nullable (e.g. hidden subs).
STAGING_SCHEMA: list[tuple[str, str]] = [
    ("youtube_snapshot_id", "STRING"),
    ("artist_id", "INT64"),
    ("snapshot_date", "DATE"),
    ("official_subscribers", "INT64"),
    ("official_total_views", "INT64"),
    ("official_video_count", "INT64"),
    ("topic_total_views", "INT64"),
    ("topic_video_count", "INT64"),
    ("load_ts_utc", "TIMESTAMP"),
]
_LEGACY_TYPES = {"INT64": "INTEGER", "BOOL": "BOOLEAN"}

_MEASURES = ("official_subscribers", "official_total_views", "official_video_count",
             "topic_total_views", "topic_video_count")


# ---------------------------------------------------------------------------
# Pure transform (unit-tested offline; no network, no BigQuery)
# ---------------------------------------------------------------------------

def dt_from_path(path: str) -> str | None:
    m = _DT_RE.search(str(path))
    return m.group(1) if m else None


def _to_int_or_none(value) -> int | None:
    """Coerce a measure to int, preserving NULL (hidden subscriber counts stay NULL)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def payload_to_rows(payload: dict, snapshot_date: str, load_ts: str) -> list[dict]:
    """Flatten one daily YouTube payload into fact_youtube rows (one per artist)."""
    rows = []
    for rec in payload.get("records", []):
        artist = rec.get("query")
        if not artist:
            continue
        aid = artist_id(artist)
        row = {
            "youtube_snapshot_id": snapshot_id("youtube", aid, snapshot_date),
            "artist_id": aid,
            "snapshot_date": snapshot_date,
            "load_ts_utc": load_ts,
        }
        for m in _MEASURES:
            row[m] = _to_int_or_none(rec.get(m))
        rows.append(row)
    return rows


def rows_from_payloads(payloads: list[tuple[str, dict]], load_ts: str) -> list[dict]:
    """Flatten ``(snapshot_date, payload)`` pairs into all fact_youtube rows."""
    rows: list[dict] = []
    for snapshot_date, payload in payloads:
        rows.extend(payload_to_rows(payload, snapshot_date, load_ts))
    return rows


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """One row per youtube_snapshot_id (last wins) — collapses multiple runs in a day."""
    by_id = {r["youtube_snapshot_id"]: r for r in rows}
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
    """List the bronze YouTube capture URIs, optionally bounded by ``dt=``."""
    pattern = f"gs://{project}-raw/{SOURCE}/dt=*/{SOURCE}_*.json"
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
    """MERGE fact_youtube rows into BigQuery on youtube_snapshot_id; return rows affected.

    Mirrors the tm_events / fact_trends upsert: load into a truncated staging table, then
    a single atomic MERGE — idempotent, so re-running the same days is a no-op.
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
    update_cols = [c for c in cols if c != "youtube_snapshot_id"]
    merge_sql = (
        f"MERGE `{fq}` T\nUSING `{staging}` S\n"
        f"ON T.youtube_snapshot_id = S.youtube_snapshot_id\n"
        f"WHEN MATCHED THEN UPDATE SET\n  "
        + ",\n  ".join(f"T.{c} = S.{c}" for c in update_cols)
        + f"\nWHEN NOT MATCHED THEN INSERT ({', '.join(cols)})\n"
        f"VALUES ({', '.join(f'S.{c}' for c in cols)})"
    )
    job = client.query(merge_sql)
    job.result()
    return int(job.num_dml_affected_rows or 0)


def summarize(rows: list[dict]) -> dict:
    subs = [r["official_subscribers"] for r in rows if r["official_subscribers"] is not None]
    return {
        "rows": len(rows),
        "artists": len({r["artist_id"] for r in rows}),
        "snapshot_dates": len({r["snapshot_date"] for r in rows}),
        "with_subscribers": len(subs),
        "subs_min": min(subs, default=None),
        "subs_max": max(subs, default=None),
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
    print(f"[youtube_to_silver] {summary}")

    if not rows:
        print("[youtube_to_silver] no rows found", file=sys.stderr)
        return 1
    if args.dry_run:
        print("[youtube_to_silver] dry-run: nothing written")
        return 0

    affected = upsert_to_silver(rows, args.project, args.dataset)
    print(f"[youtube_to_silver] merged {affected} rows into "
          f"{args.project}.{args.dataset}.{FACT_TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
