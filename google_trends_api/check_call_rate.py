#!/usr/bin/env python3
"""Deterministic read-only QC: how many Google Trends API calls we made per UTC day.

Every Trends fetch the pipeline makes lands exactly one JSON file under the bronze
partition ``google_trends/dt=<UTC-date>/``, so COUNTING that partition is a precise,
deterministic, re-runnable ledger of calls/day — across the daily job, the backfill,
and manual runs alike (they all write there). Use it to confirm we stayed under the
rate limit / daily budget without re-hitting the API.

Run (from repo root, music-demand env; needs ADC —
``gcloud auth application-default login``):

    python google_trends_api/check_call_rate.py                       # today (UTC)
    python google_trends_api/check_call_rate.py --days 7              # last 7 UTC days
    python google_trends_api/check_call_rate.py --date 2026-06-16 --budget 800
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root for `common`
from common import gcs_io  # noqa: E402
from google.cloud import storage  # noqa: E402

# google_trends_<suffix>_<YYYYMMDDTHHMMSSZ>.json — the suffix encodes the call kind
# (see fetch_and_land.suffix_for); the stamp is the capture time.
_NAME = re.compile(r"google_trends_(?P<suffix>.+)_(?P<stamp>\d{8}T\d{6}Z)\.json$")

KINDS = ("national", "snapshot", "dma", "hourly", "other")


def classify(suffix: str) -> str:
    """Map a filename suffix back to its call kind (see fetch_and_land.suffix_for).

    national = ``iot_US_*`` (underscore), dma = ``iot_US-*`` (hyphenated geo code),
    snapshot = ``ibr_DMA_*``, hourly = ``iot_now7d_US_*``.
    """
    if suffix.startswith("ibr_DMA_"):
        return "snapshot"
    if suffix.startswith("iot_now7d_US_"):
        return "hourly"
    if suffix.startswith("iot_US_"):
        return "national"
    if suffix.startswith("iot_US-"):
        return "dma"
    return "other"


def summarize_date(bucket: str, date: str) -> dict:
    """Deterministic call ledger for one UTC date's partition (one GCS listing)."""
    client = storage.Client(project=gcs_io.PROJECT_ID)
    kinds = {k: 0 for k in KINDS}
    stamps: list[datetime] = []
    total = 0
    for blob in client.list_blobs(bucket, prefix=f"google_trends/dt={date}/"):
        total += 1
        m = _NAME.search(blob.name)
        if not m:
            kinds["other"] += 1
            continue
        kinds[classify(m.group("suffix"))] += 1
        stamps.append(
            datetime.strptime(m.group("stamp"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        )
    first = min(stamps) if stamps else None
    last = max(stamps) if stamps else None
    span_min = ((last - first).total_seconds() / 60.0) if first and last else 0.0
    rate_per_min = (total / span_min) if span_min > 0 else None
    return {
        "date": date, "total": total, "kinds": kinds,
        "first": first, "last": last, "span_min": span_min,
        "rate_per_min": rate_per_min,
    }


def format_row(s: dict, budget: int | None) -> str:
    """One compact human-readable line for a day's summary."""
    k = s["kinds"]
    window = (
        f"{s['first'].strftime('%H:%M:%S')}..{s['last'].strftime('%H:%M:%SZ')}"
        if s["first"] else "—"
    )
    rate = (
        f"{s['rate_per_min']:.2f}/min ({s['rate_per_min'] * 60:.0f}/hr)"
        if s["rate_per_min"] is not None else "—"
    )
    line = (
        f"dt={s['date']}  calls={s['total']:<4d} "
        f"[national={k['national']} snapshot={k['snapshot']} dma={k['dma']} "
        f"hourly={k['hourly']} other={k['other']}]  "
        f"window={window} span={s['span_min']:.0f}m  rate={rate}"
    )
    if budget and budget > 0:
        left = budget - s["total"]
        line += f"  budget={budget} -> {'OK' if left >= 0 else 'OVER'} ({left} left)"
    return line


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--date", help="UTC date YYYY-MM-DD (default: today).")
    ap.add_argument("--days", type=int, default=1, help="Summarize the last N UTC days (default 1).")
    ap.add_argument("--bucket", default=gcs_io.DEFAULT_RAW_BUCKET, help="Bronze bucket.")
    ap.add_argument("--budget", type=int, default=0, help="Daily call budget to flag against (0 = none).")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    end = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date else datetime.now(timezone.utc).date()
    )
    dates = [(end - timedelta(days=d)).isoformat() for d in range(max(args.days, 1))]

    print(f"google_trends call rate — gs://{args.bucket}")
    for date in dates:
        print(format_row(summarize_date(args.bucket, date), args.budget or None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
