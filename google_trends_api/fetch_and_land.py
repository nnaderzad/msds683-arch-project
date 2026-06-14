#!/usr/bin/env python3
"""Fetch Google Trends interest and land raw JSON in the bronze bucket.

Reusable fetch+land core for both the local smoke test and the Cloud Run backfill
job (``job.py``). Everything routes through ``run_unit`` so the CLI, the job, and
the idempotency checkpoint all agree on what a "unit" is and how its file is named.

Unit kinds:
  national   interest_over_time(query, geo="US", ~270-day daily window)   1 call
  snapshot   interest_by_region(query, resolution="DMA") -> all ~210 DMAs  1 call
  dma        interest_over_time(query, geo="US-<ST>-<DMA>", daily window)  1 call/DMA
  hourly     interest_over_time(query, geo="US", "now 7-d")                1 call
             ^ hourly resolution — the ONE window Google can't backfill later,
               so the daily collector captures it continuously.

Each result is wrapped with metadata and written via ``common/gcs_io.upload_raw``:
  gs://<raw>/google_trends/dt=<date>/google_trends_<suffix>_<stamp>.json
Append-only bronze snapshots, one (artist, geo, endpoint) per file.

Run (from repo root, music-demand env):
    python google_trends_api/fetch_and_land.py --limit 3 --modes national snapshot --dry-run
    python google_trends_api/fetch_and_land.py --limit 3 --modes national snapshot
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Make the repo-root `common` package importable while keeping this module's own
# directory on sys.path (so `trends_client` resolves) — run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import gcs_io  # noqa: E402

from trends_client import TrendsClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fetch_and_land")

SOURCE = "google_trends"
REPO_ROOT = Path(__file__).resolve().parents[1]

# Google Trends returns DAILY granularity for windows up to ~269 days; beyond that
# it switches to weekly. 269 days gets the widest daily window in a single call.
DAILY_WINDOW_DAYS = 269
SNAPSHOT_TIMEFRAME = "today 12-m"  # window for the DMA geographic-distribution snapshot
HOURLY_TIMEFRAME = "now 7-d"       # hourly; the non-backfillable recent window


def daily_timeframe(today: datetime | None = None) -> str:
    """A 'YYYY-MM-DD YYYY-MM-DD' window that yields daily resolution from Trends."""
    end = (today or datetime.now(timezone.utc)).date()
    start = end - timedelta(days=DAILY_WINDOW_DAYS)
    return f"{start.isoformat()} {end.isoformat()}"


def slug(text: str) -> str:
    """Filesystem-safe tag for an artist name."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-")[:60] or "x"


def suffix_for(kind: str, artist: str, geo_code: str | None = None) -> str:
    """The filename suffix for a unit — used for landing AND the resume checkpoint."""
    s = slug(artist)
    return {
        "national": f"iot_US_{s}",
        "snapshot": f"ibr_DMA_{s}",
        "hourly": f"iot_now7d_US_{s}",
        "dma": f"iot_{geo_code}_{s}",
    }[kind]


def latest_roster_dir() -> Path:
    base = REPO_ROOT / "data" / "google_trends" / "roster"
    dirs = sorted(p for p in base.glob("*") if p.is_dir())
    if not dirs:
        raise SystemExit("No roster found; run build_roster.py first.")
    return dirs[-1]


def wrap(
    *, endpoint, artist, query, geo, geo_code, resolution, timeframe, granularity, frame
) -> dict:
    """Wrap a pytrends frame + metadata into the JSON payload we land."""
    records: list[dict] = []
    if frame is not None and not frame.empty:
        flat = frame.reset_index()
        flat.columns = [str(c) for c in flat.columns]
        # to_json handles numpy ints + datetimes (ISO) cleanly, unlike json.dumps.
        records = json.loads(flat.to_json(orient="records", date_format="iso"))
    return {
        "source": SOURCE,
        "endpoint": endpoint,
        "extract_ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artist": artist,
        "query": query,
        "geo": geo,
        "geo_code": geo_code,
        "resolution": resolution,
        "timeframe": timeframe,
        "granularity": granularity,
        "n_records": len(records),
        "records": records,
    }


def land(payload: dict, suffix: str, *, dry_run: bool) -> str | None:
    if dry_run:
        logger.info("[dry-run] %s %s -> %d records (suffix=%s)",
                    payload["endpoint"], payload["geo"], payload["n_records"], suffix)
        return None
    return gcs_io.upload_raw(SOURCE, payload, suffix=suffix)


def run_unit(
    client: TrendsClient,
    kind: str,
    artist: str,
    query: str,
    *,
    geo_code: str | None = None,
    tf_daily: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Fetch one unit, wrap it, and land it. Single source of truth for all kinds."""

    if kind == "national":
        frame = client.interest_over_time(query, geo="US", timeframe=tf_daily)
        meta = dict(geo="US", geo_code="US", resolution="national",
                    timeframe=tf_daily, granularity="daily")
    elif kind == "hourly":
        frame = client.interest_over_time(query, geo="US", timeframe=HOURLY_TIMEFRAME)
        meta = dict(geo="US", geo_code="US", resolution="national",
                    timeframe=HOURLY_TIMEFRAME, granularity="hourly")
    elif kind == "snapshot":
        frame = client.interest_by_region(query, geo="US", resolution="DMA",
                                          timeframe=SNAPSHOT_TIMEFRAME)
        meta = dict(geo="US", geo_code=None, resolution="DMA",
                    timeframe=SNAPSHOT_TIMEFRAME, granularity="snapshot")
    elif kind == "dma":
        frame = client.interest_over_time(query, geo=geo_code, timeframe=tf_daily)
        meta = dict(geo=geo_code, geo_code=geo_code, resolution="dma_series",
                    timeframe=tf_daily, granularity="daily")
    else:
        raise ValueError(f"unknown unit kind: {kind}")

    endpoint = "interest_by_region" if kind == "snapshot" else "interest_over_time"
    payload = wrap(endpoint=endpoint, artist=artist, query=query, frame=frame, **meta)
    land(payload, suffix_for(kind, artist, geo_code), dry_run=dry_run)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", nargs="+", default=["national", "snapshot"],
                        choices=["national", "snapshot", "dma", "hourly"],
                        help="Which fetch units to run (default: national snapshot).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap the number of selected artists (smoke testing).")
    parser.add_argument("--sleep", type=float, default=10.0,
                        help="Polite seconds between Trends calls (default 10).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + summarize but write nothing to GCS.")
    args = parser.parse_args()

    roster_dir = latest_roster_dir()
    artists = pd.read_csv(roster_dir / "roster_artist.csv")
    artists = artists[artists["selected"]].reset_index(drop=True)
    if args.limit:
        artists = artists.head(args.limit)
    targets = pd.read_csv(roster_dir / "roster_targets.csv") if "dma" in args.modes else None

    client = TrendsClient(request_sleep=args.sleep)
    tf_daily = daily_timeframe()
    logger.info("Roster: %s | %d artists | modes=%s | daily window=%s | dry_run=%s",
                roster_dir.name, len(artists), args.modes, tf_daily, args.dry_run)

    landed = 0
    for art in artists.itertuples():
        for kind in args.modes:
            if kind == "dma":
                for tgt in targets[targets["artist"] == art.artist].itertuples():
                    run_unit(client, "dma", art.artist, art.query,
                             geo_code=tgt.geo_code, tf_daily=tf_daily, dry_run=args.dry_run)
                    landed += 1
                    client.sleep_between_requests()
            else:
                run_unit(client, kind, art.artist, art.query,
                         tf_daily=tf_daily, dry_run=args.dry_run)
                landed += 1
                client.sleep_between_requests()

    logger.info("Done: %d fetch units %s.", landed,
                "simulated (dry-run)" if args.dry_run else "landed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
