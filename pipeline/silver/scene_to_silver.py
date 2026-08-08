#!/usr/bin/env python3
"""Scene-listing bronze -> silver: ``fact_nineteenhz``, ``fact_ra``, ``fact_ticketpages``.

Parses the three scene-collection bronze prefixes landed by the daily jobs
(``terraform/gtrends/scene.tf``) into per-source silver facts, reusing the
collectors' own committed parsers so bronze->silver and collector-CSV output
can never drift apart:

  * ``nineteenhz/dt=*/*.html``  -> ``fact_nineteenhz``   (one row per listed event per day)
    via ``nineteenhz_api/collect_19hz.parse_listing``
  * ``ra/dt=*/*.json``          -> ``fact_ra``           (one row per RA event per day —
    ``attending`` re-observed daily is the buzz time-series)
    via ``ra_api/collect_ra.rows_from_listings``
  * ``ticketpages/dt=*/*.json`` -> ``fact_ticketpages``  (one row per offer per page per day —
    ``availability`` = InStock/SoldOut is the sell-out signal)
    via ``nineteenhz_api/poll_ticket_pages.rows_from_event_ld``

``snapshot_date`` is the bronze ``dt=`` partition (the observation day), so
re-polling the same listings daily accumulates honest per-day history — the same
observed-only discipline as ``tm_observations``. Venue/artist strings are kept
verbatim (join-key normalization stays in the consumers, via ``common/keys.py``).

Idempotent staging+MERGE per table on a deterministic surrogate key; no LLM.

Run (repo root; BigQuery/GCS authed via ADC):
    python pipeline/silver/scene_to_silver.py --dry-run                # parse + count only
    python pipeline/silver/scene_to_silver.py                          # all three tables
    python pipeline/silver/scene_to_silver.py --source ra --start-date 2026-07-08
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "nineteenhz_api"))
sys.path.insert(0, str(REPO_ROOT / "ra_api"))
from collect_19hz import parse_listing  # noqa: E402
from collect_ra import rows_from_listings  # noqa: E402
from poll_ticket_pages import rows_from_event_ld  # noqa: E402

from common.keys import normalize_name, snapshot_id  # noqa: E402

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"

_DT_RE = re.compile(r"dt=([0-9]{4}-[0-9]{2}-[0-9]{2})")

# One (bronze prefix, target table, staging schema, transform) bundle per source.
NINETEENHZ_SCHEMA: list[tuple[str, str]] = [
    ("nineteenhz_snapshot_id", "STRING"),
    ("snapshot_date", "DATE"),
    ("event_date", "DATE"),
    ("title", "STRING"),
    ("venue", "STRING"),
    ("city", "STRING"),
    ("genres", "STRING"),
    ("price_text", "STRING"),
    ("age_restriction", "STRING"),
    ("is_free", "BOOL"),
    ("price_min", "FLOAT64"),
    ("price_max", "FLOAT64"),
    ("price_open_ended", "BOOL"),
    ("organizers", "STRING"),
    ("artists", "STRING"),
    ("n_artists", "INT64"),
    ("ticket_url", "STRING"),
    ("ticket_domain", "STRING"),
    ("load_ts_utc", "TIMESTAMP"),
]

RA_SCHEMA: list[tuple[str, str]] = [
    ("ra_snapshot_id", "STRING"),
    ("ra_event_id", "STRING"),
    ("snapshot_date", "DATE"),
    ("event_date", "DATE"),
    ("title", "STRING"),
    ("start_time", "STRING"),
    ("end_time", "STRING"),
    ("attending", "INT64"),
    ("is_ticketed", "BOOL"),
    ("cost_text", "STRING"),
    ("venue_ra_id", "STRING"),
    ("venue", "STRING"),
    ("artists", "STRING"),
    ("n_artists", "INT64"),
    ("genres", "STRING"),
    ("event_url", "STRING"),
    ("load_ts_utc", "TIMESTAMP"),
]

TICKETPAGES_SCHEMA: list[tuple[str, str]] = [
    ("ticketpage_snapshot_id", "STRING"),
    ("snapshot_date", "DATE"),
    ("ticket_url", "STRING"),
    ("ticket_domain", "STRING"),
    ("ld_type", "STRING"),
    ("event_name", "STRING"),
    ("start_date", "DATE"),
    ("venue_name", "STRING"),
    ("event_status", "STRING"),
    ("offer_type", "STRING"),
    ("offer_name", "STRING"),
    ("availability", "STRING"),
    ("currency", "STRING"),
    ("valid_from", "STRING"),
    ("price_min", "FLOAT64"),
    ("price_max", "FLOAT64"),
    ("load_ts_utc", "TIMESTAMP"),
]

_LEGACY_TYPES = {"INT64": "INTEGER", "BOOL": "BOOLEAN", "FLOAT64": "FLOAT"}


# ---------------------------------------------------------------------------
# Pure transforms (unit-tested offline; no network, no BigQuery)
# ---------------------------------------------------------------------------

def dt_from_path(path: str) -> str | None:
    m = _DT_RE.search(str(path))
    return m.group(1) if m else None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_date(text) -> str | None:
    """Keep only clean YYYY-MM-DD strings (BQ DATE column) — else NULL."""
    s = str(text or "")[:10]
    return s if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s) else None


def nineteenhz_rows(page_html: str, capture_dt: str, load_ts: str) -> list[dict]:
    """One bronze HTML capture -> fact_nineteenhz rows (snapshot_date = capture day)."""
    rows = []
    for ev in parse_listing(page_html):
        rows.append({
            "nineteenhz_snapshot_id": snapshot_id(
                "nineteenhz", normalize_name(ev["title"]), normalize_name(ev["venue"]),
                ev["event_date"], capture_dt),
            "snapshot_date": capture_dt,
            "event_date": _valid_date(ev["event_date"]),
            "title": ev["title"],
            "venue": ev["venue"],
            "city": ev["city"],
            "genres": ev["genres"],
            "price_text": ev["price_text"],
            "age_restriction": ev["age_restriction"],
            "is_free": bool(ev["is_free"]),
            "price_min": _to_float(ev["price_min"]),
            "price_max": _to_float(ev["price_max"]),
            "price_open_ended": bool(ev["price_open_ended"]),
            "organizers": ev["organizers"],
            "artists": ev["artists"],
            "n_artists": int(ev["n_artists"] or 0),
            "ticket_url": ev["ticket_url"],
            "ticket_domain": ev["ticket_domain"],
            "load_ts_utc": load_ts,
        })
    return rows


def ra_rows(payload: dict, capture_dt: str, load_ts: str) -> list[dict]:
    """One bronze RA capture ({request_variables, extract_ts_utc, response}) -> fact_ra rows."""
    listing_rows = rows_from_listings(payload.get("response") or {},
                                      payload.get("extract_ts_utc") or "")
    rows = []
    for r in listing_rows:
        if not r["ra_event_id"]:
            continue
        rows.append({
            "ra_snapshot_id": snapshot_id("ra", r["ra_event_id"], capture_dt),
            "ra_event_id": str(r["ra_event_id"]),
            "snapshot_date": capture_dt,
            "event_date": _valid_date(r["event_date"]),
            "title": r["title"],
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "attending": r["attending"],
            "is_ticketed": r["is_ticketed"],
            "cost_text": r["cost_text"],
            "venue_ra_id": str(r["venue_ra_id"]) if r["venue_ra_id"] else None,
            "venue": r["venue"],
            "artists": r["artists"],
            "n_artists": int(r["n_artists"] or 0),
            "genres": r["genres"],
            "event_url": r["event_url"],
            "load_ts_utc": load_ts,
        })
    return rows


def ticketpage_rows(payload: list, capture_dt: str, load_ts: str) -> list[dict]:
    """One bronze JSON-LD capture ([{ticket_url, extract_ts_utc, event_ld}]) -> offer rows."""
    rows = []
    for item in payload or []:
        url = item.get("ticket_url")
        ts = item.get("extract_ts_utc") or ""
        for ld in item.get("event_ld") or []:
            for r in rows_from_event_ld(ld, url, ts):
                rows.append({
                    "ticketpage_snapshot_id": snapshot_id(
                        "ticketpages", url, r["event_name"], r["offer_type"],
                        r["offer_name"], capture_dt),
                    "snapshot_date": capture_dt,
                    "ticket_url": url,
                    "ticket_domain": r["ticket_domain"],
                    "ld_type": r["ld_type"],
                    "event_name": r["event_name"],
                    "start_date": _valid_date(r["start_date"]),
                    "venue_name": r["venue_name"],
                    "event_status": r["event_status"],
                    "offer_type": r["offer_type"],
                    "offer_name": r["offer_name"],
                    "availability": r["availability"],
                    "currency": r["currency"],
                    "valid_from": r["valid_from"],
                    "price_min": _to_float(r["price_min"]),
                    "price_max": _to_float(r["price_max"]),
                    "load_ts_utc": load_ts,
                })
    return rows


def dedupe_last(rows: list[dict], key_col: str) -> list[dict]:
    """One row per surrogate key, last occurrence wins (files are read in sorted
    order, so a re-landed capture within the same partition supersedes)."""
    best: dict[str, dict] = {}
    for r in rows:
        best[r[key_col]] = r
    return list(best.values())


SOURCES: dict[str, dict] = {
    "nineteenhz": {"prefix": "nineteenhz/", "ext": ".html", "table": "fact_nineteenhz",
                   "schema": NINETEENHZ_SCHEMA, "key": "nineteenhz_snapshot_id",
                   "transform": nineteenhz_rows, "parse": "text"},
    "ra": {"prefix": "ra/", "ext": ".json", "table": "fact_ra",
           "schema": RA_SCHEMA, "key": "ra_snapshot_id",
           "transform": ra_rows, "parse": "json"},
    "ticketpages": {"prefix": "ticketpages/", "ext": ".json", "table": "fact_ticketpages",
                    "schema": TICKETPAGES_SCHEMA, "key": "ticketpage_snapshot_id",
                    "transform": ticketpage_rows, "parse": "json"},
}


# ---------------------------------------------------------------------------
# I/O (kept thin so the transforms above stay unit-testable)
# ---------------------------------------------------------------------------

def read_bronze(project: str, spec: dict, start: str | None, end: str | None) -> list[tuple[str, object]]:
    """(capture_dt, parsed payload) per bronze file under the source prefix."""
    from google.cloud import storage

    client = storage.Client(project=project)
    bucket = f"{project}-raw"
    out = []
    blobs = [b for b in client.list_blobs(bucket, prefix=spec["prefix"])
             if b.name.endswith(spec["ext"])]
    blobs.sort(key=lambda b: b.name)
    for blob in blobs:
        dt = dt_from_path(blob.name)
        if dt is None or (start and dt < start) or (end and dt > end):
            continue
        text = blob.download_as_text()
        out.append((dt, json.loads(text) if spec["parse"] == "json" else text))
    return out


def read_fixture_dir(base: str, spec: dict) -> list[tuple[str, object]]:
    """Committed fixtures: ``<base>/<prefix>dt=YYYY-MM-DD/*<ext>``."""
    out = []
    for path in sorted((Path(base) / spec["prefix"]).glob(f"dt=*/*{spec['ext']}")):
        text = path.read_text(encoding="utf-8")
        out.append((dt_from_path(path), json.loads(text) if spec["parse"] == "json" else text))
    return out


def upsert_to_silver(rows: list[dict], spec: dict, project: str, dataset: str) -> int:
    """MERGE rows into the source's silver table on its surrogate key (idempotent)."""
    from google.cloud import bigquery  # lazy: keeps the transform import offline-safe

    fq = f"{project}.{dataset}.{spec['table']}"
    staging = f"{fq}_staging"
    key = spec["key"]
    client = bigquery.Client(project=project)

    cols_ddl = ",\n  ".join(f"{name} {bq_type}" for name, bq_type in spec["schema"])
    client.query(
        f"CREATE TABLE IF NOT EXISTS `{fq}` (\n  {cols_ddl}\n)\n"
        f"PARTITION BY snapshot_date"
    ).result()

    schema = [bigquery.SchemaField(name, _LEGACY_TYPES.get(bq_type, bq_type))
              for name, bq_type in spec["schema"]]
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(rows, staging, job_config=job_config).result()

    cols = [name for name, _ in spec["schema"]]
    update_cols = [c for c in cols if c != key]
    merge_sql = (
        f"MERGE `{fq}` T\nUSING `{staging}` S\n"
        f"ON T.{key} = S.{key}\n"
        f"WHEN MATCHED THEN UPDATE SET\n  "
        + ",\n  ".join(f"T.{c} = S.{c}" for c in update_cols)
        + f"\nWHEN NOT MATCHED THEN INSERT ({', '.join(cols)})\n"
        f"VALUES ({', '.join(f'S.{c}' for c in cols)})"
    )
    job = client.query(merge_sql)
    job.result()
    return int(job.num_dml_affected_rows or 0)


def summarize(name: str, rows: list[dict]) -> dict:
    return {
        "source": name,
        "rows": len(rows),
        "snapshot_dates": len({r["snapshot_date"] for r in rows}),
        "date_min": min((r["snapshot_date"] for r in rows), default=None),
        "date_max": max((r["snapshot_date"] for r in rows), default=None),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--source", default="all",
                        choices=["all", *SOURCES], help="which bronze source(s) to load")
    parser.add_argument("--from-fixtures", default=None,
                        help="read committed bronze fixtures from this dir instead of GCS")
    parser.add_argument("--start-date", default=None, help="earliest dt= partition (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="latest dt= partition (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="transform + summarize but write nothing to BigQuery")
    args = parser.parse_args()

    load_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    names = list(SOURCES) if args.source == "all" else [args.source]

    total = 0
    for name in names:
        spec = SOURCES[name]
        if args.from_fixtures:
            captures = read_fixture_dir(args.from_fixtures, spec)
        else:
            captures = read_bronze(args.project, spec, args.start_date, args.end_date)
        rows: list[dict] = []
        for capture_dt, payload in captures:
            rows.extend(spec["transform"](payload, capture_dt, load_ts))
        rows = dedupe_last(rows, spec["key"])
        print(f"[scene_to_silver] {summarize(name, rows)}")
        total += len(rows)
        if not rows or args.dry_run:
            continue
        affected = upsert_to_silver(rows, spec, args.project, args.dataset)
        print(f"[scene_to_silver] merged {affected} rows into "
              f"{args.project}.{args.dataset}.{spec['table']}")

    if total == 0:
        print("[scene_to_silver] no rows found in any requested source", file=sys.stderr)
        return 1
    if args.dry_run:
        print("[scene_to_silver] dry-run: nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
