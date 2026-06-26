#!/usr/bin/env python3
"""Build the conformed silver dimensions + bridge (task A3).

Derives the dimensions the whole constellation + gold star share, per the locked schema
(``docs/data-model.md`` §3), from the current-state ``tm_events`` table plus committed
reference crosswalks:

  dim_geo     <- google_trends_api/reference/dma_geo.csv  (210 Nielsen DMAs)
  dim_date    <- generated calendar (US holidays via the `holidays` library)
  dim_venue   <- distinct tm_events venues, DMA via GeoLookup (ZIP-first, ~99.5%)
  dim_artist  <- distinct tm_events attractions, enriched from the roster + YT cache
  dim_event   <- one row per tm_events event
  bridge_event_artist <- event x attraction, headliner by name-match (else first)

Dimensions are **current-state** (SCD deferred), so each table is a full-refresh
(``WRITE_TRUNCATE``) — idempotent and re-runnable. Keys reuse ``common/keys.py`` so the
surrogates match the facts (A1/A2) without a build-order dependency.

The builder functions are **pure** (all data injected) and unit-test offline on the seed
``tm_events`` fixture; BigQuery / GCS / the holidays library live only in the I/O layer.

Run (repo root; BigQuery authed via ADC; needs `pip install -r pipeline/requirements.txt`):
    python pipeline/silver/build_dimensions.py --dry-run
    python pipeline/silver/build_dimensions.py
    python pipeline/silver/build_dimensions.py --from-fixtures tests/fixtures/seed/ticketmaster/tm_events_seed.csv
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "google_trends_api"))
from common.keys import artist_id, normalize_name, venue_id  # noqa: E402
from geo_lookup import GeoLookup  # noqa: E402

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"
DMA_GEO_CSV = REPO_ROOT / "google_trends_api" / "reference" / "dma_geo.csv"
ROSTER_GLOB = "roster_artist_*.csv"
ROSTER_DIR = REPO_ROOT / "google_trends_api" / "sample_data"
CALENDAR_FLOOR = date(2026, 6, 1)  # covers the earliest daily snapshot partition

# tm_events columns the dimension build reads.
TM_COLUMNS = [
    "event_id", "event_name", "event_type", "event_url", "local_date", "local_time",
    "timezone", "genre", "venue_id", "venue_name", "venue_city", "venue_state_code",
    "venue_postal_code", "venue_address", "venue_latitude", "venue_longitude",
    "attraction_ids", "attraction_names",
]

_SEASON = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
           6: "Summer", 7: "Summer", 8: "Summer", 9: "Fall", 10: "Fall", 11: "Fall"}

# (table -> [(column, BigQuery type)]) — matches docs/data-model.md §3.
SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "dim_geo": [("dma_code", "STRING"), ("geo_code", "STRING"),
                ("metro_name", "STRING"), ("state", "STRING")],
    "dim_date": [("date", "DATE"), ("year", "INT64"), ("quarter", "INT64"),
                 ("month", "INT64"), ("month_name", "STRING"), ("day_of_week", "INT64"),
                 ("day_name", "STRING"), ("is_weekend", "BOOL"),
                 ("is_us_holiday", "BOOL"), ("season", "STRING")],
    "dim_venue": [("venue_id", "INT64"), ("ticketmaster_venue_id", "STRING"),
                  ("venue_name", "STRING"), ("dma_code", "STRING"),
                  ("ticketmaster_market_id", "STRING"), ("capacity", "INT64"),
                  ("venue_type", "STRING"), ("city", "STRING"), ("state_code", "STRING"),
                  ("postal_code", "STRING"), ("address", "STRING"),
                  ("latitude", "FLOAT64"), ("longitude", "FLOAT64")],
    "dim_artist": [("artist_id", "INT64"), ("artist_name", "STRING"),
                   ("trends_query", "STRING"), ("primary_genre", "STRING"),
                   ("yt_channel_id", "STRING"), ("ticketmaster_attraction_id", "STRING")],
    "dim_event": [("event_id", "STRING"), ("event_name", "STRING"),
                  ("event_type", "STRING"), ("show_date", "DATE"), ("show_time", "STRING"),
                  ("timezone", "STRING"), ("primary_genre", "STRING"),
                  ("event_url", "STRING"), ("venue_id", "INT64")],
    "bridge_event_artist": [("event_id", "STRING"), ("artist_id", "INT64"),
                            ("is_headliner", "BOOL"), ("billing_order", "INT64")],
}
_LEGACY_TYPES = {"INT64": "INTEGER", "BOOL": "BOOLEAN", "FLOAT64": "FLOAT"}


# ---------------------------------------------------------------------------
# Pure builders (unit-tested offline; data injected, no network)
# ---------------------------------------------------------------------------

def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _split(piped: str | None) -> list[str]:
    return [p for p in (piped or "").split("|") if p.strip()]


def build_dim_geo(dma_geo_rows: list[dict]) -> list[dict]:
    """reference/dma_geo.csv -> dim_geo (rename dma->dma_code, dma_name->metro_name)."""
    return [{"dma_code": r.get("dma"), "geo_code": r.get("geo_code"),
             "metro_name": r.get("dma_name"), "state": r.get("state")}
            for r in dma_geo_rows]


def date_features(d: date) -> dict:
    """Calendar features derivable from the date alone (no holiday lookup)."""
    return {
        "date": d.isoformat(),
        "year": d.year,
        "quarter": (d.month - 1) // 3 + 1,
        "month": d.month,
        "month_name": calendar.month_name[d.month],
        "day_of_week": d.isoweekday(),          # 1=Mon .. 7=Sun
        "day_name": calendar.day_name[d.weekday()],
        "is_weekend": d.isoweekday() >= 6,
        "season": _SEASON[d.month],
    }


def build_dim_date(start: date, end: date, holiday_dates: set[date]) -> list[dict]:
    """Inclusive calendar from start..end; is_us_holiday from the injected holiday set."""
    out = []
    d = start
    while d <= end:
        feat = date_features(d)
        feat["is_us_holiday"] = d in holiday_dates
        out.append(feat)
        d += timedelta(days=1)
    return out


def build_dim_venue(tm_rows: list[dict], geo: GeoLookup) -> list[dict]:
    """Distinct tm_events venues; dma_code via GeoLookup (ZIP-first, state fallback)."""
    by_tmv: dict[str, dict] = {}
    for r in tm_rows:
        tmv = r.get("venue_id")
        if not tmv:
            continue
        hit = geo.resolve(zip_code=r.get("venue_postal_code"), state=r.get("venue_state_code"))
        by_tmv[tmv] = {
            "venue_id": venue_id(tmv),
            "ticketmaster_venue_id": tmv,
            "venue_name": r.get("venue_name"),
            "dma_code": hit.dma if hit else None,
            "ticketmaster_market_id": None,
            "capacity": None,            # later web/SeatGeek backfill
            "venue_type": None,
            "city": r.get("venue_city"),
            "state_code": r.get("venue_state_code"),
            "postal_code": r.get("venue_postal_code"),
            "address": r.get("venue_address"),
            "latitude": _to_float(r.get("venue_latitude")),
            "longitude": _to_float(r.get("venue_longitude")),
        }
    return list(by_tmv.values())


def build_dim_artist(tm_rows: list[dict], roster: dict[str, dict],
                     channel_cache: dict[str, str]) -> list[dict]:
    """Distinct attractions -> dim_artist, best-effort enriched from roster + YT cache.

    ``roster`` / ``channel_cache`` are keyed on normalize_name(artist).
    """
    artists: dict[str, dict] = {}
    for r in tm_rows:
        names = _split(r.get("attraction_names"))
        ids = _split(r.get("attraction_ids"))
        genre = (r.get("genre") or "").strip()
        for idx, name in enumerate(names):
            norm = normalize_name(name)
            if not norm:
                continue
            a = artists.setdefault(norm, {"name": name, "genres": {},
                                          "tm_id": ids[idx] if idx < len(ids) else None})
            if genre:
                a["genres"][genre] = a["genres"].get(genre, 0) + 1
    out = []
    for norm, a in artists.items():
        rec = roster.get(norm) or {}
        mf_genre = None
        if a["genres"]:
            mf_genre = sorted(a["genres"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out.append({
            "artist_id": artist_id(a["name"]),
            "artist_name": a["name"],
            "trends_query": (rec.get("query") or "").strip() or a["name"],
            "primary_genre": (rec.get("top_genre") or "").strip() or mf_genre,
            "yt_channel_id": channel_cache.get(norm),
            "ticketmaster_attraction_id": a["tm_id"],
        })
    return out


def pick_headliner_index(event_name: str | None, names: list[str]) -> int:
    """Headliner = the attraction whose name appears in the event title, else the first."""
    ename = normalize_name(event_name)
    for i, n in enumerate(names):
        norm = normalize_name(n)
        if norm and norm in ename:
            return i
    return 0


def build_bridge_event_artist(tm_rows: list[dict]) -> list[dict]:
    """event x attraction rows with is_headliner (name-match) + billing_order (position)."""
    by_event: dict[str, dict] = {}
    for r in tm_rows:
        eid = r.get("event_id")
        if eid:
            by_event[eid] = r  # current-state: last wins
    out = []
    for eid, r in by_event.items():
        names = _split(r.get("attraction_names"))
        if not names:
            continue
        head = pick_headliner_index(r.get("event_name"), names)
        seen: set[int] = set()
        for i, name in enumerate(names):
            aid = artist_id(name)
            if aid in seen:
                continue  # same artist listed twice on one event
            seen.add(aid)
            out.append({"event_id": eid, "artist_id": aid,
                        "is_headliner": i == head, "billing_order": i + 1})
    return out


def build_dim_event(tm_rows: list[dict]) -> list[dict]:
    """One row per event_id (current-state)."""
    by_id: dict[str, dict] = {}
    for r in tm_rows:
        eid = r.get("event_id")
        if not eid:
            continue
        tmv = r.get("venue_id")
        by_id[eid] = {
            "event_id": eid,
            "event_name": r.get("event_name"),
            "event_type": r.get("event_type"),
            "show_date": (r.get("local_date") or None) or None,
            "show_time": r.get("local_time") or None,
            "timezone": r.get("timezone") or None,
            "primary_genre": r.get("genre") or None,
            "event_url": r.get("event_url"),
            "venue_id": venue_id(tmv) if tmv else None,
        }
    return list(by_id.values())


# ---------------------------------------------------------------------------
# I/O (isolated so the builders above stay unit-testable)
# ---------------------------------------------------------------------------

def read_reference_geo() -> list[dict]:
    with DMA_GEO_CSV.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_roster() -> dict[str, dict]:
    """Latest committed roster -> {normalized_artist: {query, top_genre}} (best-effort)."""
    files = sorted(ROSTER_DIR.glob(ROSTER_GLOB))
    if not files:
        return {}
    out = {}
    with files[-1].open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[normalize_name(row.get("artist"))] = {
                "query": row.get("query"), "top_genre": row.get("top_genre")}
    return out


def read_channel_cache(project: str) -> dict[str, str]:
    """YouTube channel cache -> {normalized_artist: official_channel_id} (best-effort)."""
    uri = f"gs://{project}-processed/youtube/channel_cache.json"
    try:
        proc = subprocess.run(["gsutil", "cat", uri],
                              capture_output=True, text=True, check=True)
        cache = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}
    return {normalize_name(name): entry.get("official_id")
            for name, entry in cache.items() if entry.get("official_id")}


def _jsonify(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def read_tm_events_bq(project: str, dataset: str) -> list[dict]:
    from google.cloud import bigquery  # lazy: keeps the builders import offline-safe

    client = bigquery.Client(project=project)
    sql = f"SELECT {', '.join(TM_COLUMNS)} FROM `{project}.{dataset}.tm_events`"
    return [{k: _jsonify(v) for k, v in dict(row).items()}
            for row in client.query(sql).result()]


def read_tm_events_fixture(path: str) -> list[dict]:
    with Path(path).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def us_holiday_dates(years: range) -> set[date]:
    import holidays  # lazy: only the real load needs the library

    return set(holidays.US(years=list(years)).keys())


def date_span(tm_rows: list[dict]) -> tuple[date, date]:
    """Calendar range = CALENDAR_FLOOR (covers snapshots) .. latest show date."""
    shows = []
    for r in tm_rows:
        try:
            shows.append(date.fromisoformat(str(r.get("local_date"))[:10]))
        except (TypeError, ValueError):
            continue
    end = max(shows) if shows else CALENDAR_FLOOR
    start = min([CALENDAR_FLOOR, *shows]) if shows else CALENDAR_FLOOR
    return start, max(end, CALENDAR_FLOOR)


def replace_table(rows: list[dict], table: str, project: str, dataset: str) -> int:
    """CREATE-OR-REPLACE load one dimension table (full refresh); return row count."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    schema = [bigquery.SchemaField(name, _LEGACY_TYPES.get(t, t))
              for name, t in SCHEMAS[table]]
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(
        rows, f"{project}.{dataset}.{table}", job_config=job_config).result()
    return len(rows)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def build_all(tm_rows: list[dict], geo: GeoLookup, roster: dict, channel_cache: dict,
              holiday_dates: set[date]) -> dict[str, list[dict]]:
    start, end = date_span(tm_rows)
    return {
        "dim_geo": build_dim_geo(read_reference_geo()),
        "dim_date": build_dim_date(start, end, holiday_dates),
        "dim_venue": build_dim_venue(tm_rows, geo),
        "dim_artist": build_dim_artist(tm_rows, roster, channel_cache),
        "dim_event": build_dim_event(tm_rows),
        "bridge_event_artist": build_bridge_event_artist(tm_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--from-fixtures", default=None,
                        help="read tm_events rows from this CSV instead of BigQuery")
    parser.add_argument("--dry-run", action="store_true",
                        help="build + summarize but write nothing to BigQuery")
    args = parser.parse_args()

    if args.from_fixtures:
        tm_rows = read_tm_events_fixture(args.from_fixtures)
    else:
        tm_rows = read_tm_events_bq(args.project, args.dataset)

    start, end = date_span(tm_rows)
    holiday_dates = us_holiday_dates(range(start.year, end.year + 1))
    tables = build_all(tm_rows, GeoLookup(), read_roster(),
                       read_channel_cache(args.project), holiday_dates)

    print(f"[build_dimensions] tm_rows={len(tm_rows)} "
          f"calendar={start}..{end} | "
          + ", ".join(f"{t}={len(rows)}" for t, rows in tables.items()))

    if args.dry_run:
        print("[build_dimensions] dry-run: nothing written")
        return 0

    for table, rows in tables.items():
        n = replace_table(rows, table, args.project, args.dataset)
        print(f"[build_dimensions] replaced {args.project}.{args.dataset}.{table} ({n} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
