#!/usr/bin/env python3
"""music-demand Google Trends POC: pull local search interest for artists.

For each (artist, metro) pair we fetch Google Trends interest-over-time and emit
one tidy CSV (long format), ready to join to Ticketmaster events and Spotify
popularity downstream.

Examples
--------
    # Activate the project env first:
    #   conda activate music-demand

    # Default: all roster artists across the 10 US metros, weekly, ~90 days.
    python google_trends_api/fetch_trends.py --output data/google_trends/interest.csv

    # Just the Bay Area, EDM artists only, as a quick smoke test:
    python google_trends_api/fetch_trends.py --geo bay-area --category edm --limit 5

    # Daily granularity instead of weekly, custom window:
    python google_trends_api/fetch_trends.py --granularity daily --timeframe "today 1-m"

    # See the metros we track (and their Google Trends geo codes):
    python google_trends_api/fetch_trends.py --list-metros

Output columns (tidy / long format)
------------------------------------
    extract_ts_utc, artist, query, category, geo_name, geo_code, dma,
    date, granularity, interest, is_partial
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from trends_client import TrendsClient, to_weekly

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fetch_trends")

OUTPUT_FIELDS = [
    "extract_ts_utc",
    "artist",
    "query",
    "category",
    "geo_name",
    "geo_code",
    "dma",
    "date",
    "granularity",
    "interest",
    "is_partial",
]


def select_artists(args: argparse.Namespace) -> list[config.Artist]:
    """Resolve the --category / --limit filters into a concrete artist list."""

    artists = config.ARTISTS
    if args.category:
        artists = config.artists_by_category(args.category)
        if not artists:
            raise SystemExit(f"No artists in category '{args.category}'.")
    if args.limit:
        artists = artists[: args.limit]
    return artists


def select_metros(args: argparse.Namespace) -> list[config.Metro]:
    """Resolve the --geo preset into a concrete metro list."""

    preset = config.GEO_PRESETS.get(args.geo)
    if preset is None:
        raise SystemExit(
            f"Unknown --geo '{args.geo}'. Choices: {', '.join(config.GEO_PRESETS)}"
        )
    return preset


def build_rows(
    artist: config.Artist,
    metro: config.Metro,
    frame: Any,
    granularity: str,
    extract_ts: str,
) -> list[dict[str, Any]]:
    """Flatten one pytrends frame into tidy long-format rows."""

    if frame is None or frame.empty or artist.query not in frame.columns:
        return []

    daily = frame[artist.query]
    partial = frame.get("isPartial")

    if granularity == "weekly":
        series = to_weekly(daily)
        # A week is "partial" if any constituent day was still being collected.
        partial_weekly = (
            partial.resample("W").max() if partial is not None else None
        )
    else:
        series = daily
        partial_weekly = partial

    rows: list[dict[str, Any]] = []
    for date, value in series.items():
        if value is None or (hasattr(value, "__float__") and value != value):  # NaN
            continue
        is_partial = bool(partial_weekly[date]) if partial_weekly is not None else None
        rows.append(
            {
                "extract_ts_utc": extract_ts,
                "artist": artist.name,
                "query": artist.query,
                "category": artist.category,
                "geo_name": metro.name,
                "geo_code": metro.geo,
                "dma": metro.dma,
                "date": date.date().isoformat(),
                "granularity": granularity,
                "interest": int(value),
                "is_partial": is_partial,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write tidy rows to CSV, creating parent dirs as needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows to %s", len(rows), path)


def print_metros() -> None:
    """List the metros we track and their Google Trends geo codes."""

    print(f"{'Metro':36s} {'geo code':14s} DMA")
    print("-" * 58)
    for metro in config.US_METROS:
        print(f"{metro.name:36s} {metro.geo:14s} {metro.dma}")


def print_coverage_summary(rows: list[dict[str, Any]], n_artists: int, n_metros: int) -> None:
    """Print how many (artist, metro) series actually returned data."""

    pairs_with_data = {(r["artist"], r["geo_name"]) for r in rows}
    expected = n_artists * n_metros
    print("\nCoverage summary:")
    print(f"- (artist x metro) pairs requested: {expected}")
    print(f"- pairs with >=1 data point:        {len(pairs_with_data)}")
    print(f"- total rows written:               {len(rows)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="music-demand Google Trends POC fetcher")
    parser.add_argument(
        "--geo",
        default=config.DEFAULT_GEO_PRESET,
        help=f"Metro preset: {', '.join(config.GEO_PRESETS)} (default: %(default)s)",
    )
    parser.add_argument(
        "--category",
        help="Only fetch one artist category (edm, pop, hiphop, rnb, festival).",
    )
    parser.add_argument(
        "--limit", type=int, help="Cap the number of artists (useful for smoke tests)."
    )
    parser.add_argument(
        "--timeframe",
        default="today 3-m",
        help="Google Trends timeframe string (default: %(default)s = ~90 days).",
    )
    parser.add_argument(
        "--granularity",
        choices=["weekly", "daily"],
        default="weekly",
        help="weekly resamples daily data to steadier weekly means (default).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=10.0,
        help="Seconds to pause between queries to avoid HTTP 429 (default: %(default)s).",
    )
    parser.add_argument("--output", type=Path, help="CSV output path.")
    parser.add_argument(
        "--list-metros", action="store_true", help="Print tracked metros and exit."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list_metros:
        print_metros()
        return 0

    artists = select_artists(args)
    metros = select_metros(args)
    client = TrendsClient(request_sleep=args.sleep)
    extract_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    total = len(artists) * len(metros)
    logger.info(
        "Fetching %d artists x %d metros = %d series (%s, %s)",
        len(artists), len(metros), total, args.granularity, args.timeframe,
    )

    rows: list[dict[str, Any]] = []
    done = 0
    for artist in artists:
        for metro in metros:
            done += 1
            logger.info("[%d/%d] %s @ %s", done, total, artist.name, metro.name)
            frame = client.interest_over_time(artist.query, metro.geo, args.timeframe)
            new_rows = build_rows(artist, metro, frame, args.granularity, extract_ts)
            if not new_rows:
                logger.info("    no data for %s @ %s", artist.name, metro.name)
            rows.extend(new_rows)
            # Polite pause between queries, but not after the final one.
            if done < total:
                client.sleep_between_requests()

    print_coverage_summary(rows, len(artists), len(metros))

    if args.output:
        write_csv(args.output, rows)
    else:
        logger.info("No --output given; not writing a CSV (dry run).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
