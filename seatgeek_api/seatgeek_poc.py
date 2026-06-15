#!/usr/bin/env python3
"""SeatGeek Platform API proof-of-concept extractor.

Why SeatGeek (alongside Ticketmaster): Ticketmaster's Discovery API only exposes
a *face-value* price for ~23% of events (profiled in analysis/profile_schema.py).
SeatGeek's /events `stats` carry **secondary-market** signals — `lowest_price`,
`average_price`, `listing_count` (resale inventory = a demand proxy) — which fit a
*resale*-demand thesis better, and act as a fallback if Ticketmaster breaks.

Like Ticketmaster prices and YouTube stats, SeatGeek's `stats` are a CURRENT
snapshot — there is no historical price series in the API — so history is built
the same way: snapshot on a schedule (this script, re-run daily, is forward-only).

Bonus: SeatGeek venue objects sometimes carry `capacity`, which is otherwise a
hand-gathered dimension for us — this POC measures how often it's populated.

Usage:
  # 1. Get a client_id (see seatgeek_api/README.md — access now needs approval).
  #    Put it in a git-ignored seatgeek_api/.env  ->  SEATGEEK_CLIENT_ID=xxxx
  #    or export it:  export SEATGEEK_CLIENT_ID="xxxx"
  python seatgeek_api/seatgeek_poc.py --city "San Francisco" --state CA --output sf_seatgeek.csv
  python seatgeek_api/seatgeek_poc.py --query "Greek Theatre"

Standard library only, so it runs in a fresh environment without pip installs
(optionally uses certifi for HTTPS if present, like the Ticketmaster POC).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import ssl
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# SeatGeek Platform API v2 root (https://platform.seatgeek.com/).
BASE_URL = "https://api.seatgeek.com/2"


def load_env() -> None:
    """Load KEY=VALUE pairs from seatgeek_api/.env into os.environ.

    Same git-ignored .env pattern as the Ticketmaster POC, so the client_id
    doesn't have to be exported every session.
    """

    from pathlib import Path

    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def get_client_id() -> str:
    """Read the SeatGeek client_id from the environment."""

    client_id = os.getenv("SEATGEEK_CLIENT_ID")
    if not client_id:
        raise SystemExit(
            "Missing SEATGEEK_CLIENT_ID. Register an app at "
            "https://seatgeek.com/account/develop (see seatgeek_api/README.md), "
            "then: export SEATGEEK_CLIENT_ID='your_client_id'"
        )
    return client_id


def build_ssl_context() -> ssl.SSLContext:
    """Use certifi's CA bundle when available (fixes some Anaconda CA paths)."""

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def fetch_json(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Call one SeatGeek endpoint and return its JSON response.

    client_id is required on every request; client_secret is optional and only
    added if present in the environment.
    """

    query_params: dict[str, Any] = {"client_id": get_client_id(), **params}
    if os.getenv("SEATGEEK_CLIENT_SECRET"):
        query_params["client_secret"] = os.environ["SEATGEEK_CLIENT_SECRET"]

    url = f"{BASE_URL}/{endpoint}?{urlencode(query_params, doseq=True)}"
    request = Request(url, headers={"User-Agent": "msds-data-architecture-poc/1.0"})

    try:
        with urlopen(request, timeout=30, context=build_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        hint = " (401/403 usually means a missing/unapproved client_id)" if exc.code in (401, 403) else ""
        raise SystemExit(f"SeatGeek HTTP {exc.code}{hint}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"Could not reach SeatGeek API: {exc.reason}") from exc


def iso_utc(dt: datetime) -> str:
    """ISO-8601 the way SeatGeek's datetime filters expect: YYYY-MM-DDTHH:MM:SS."""

    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def search_events(
    city: str | None,
    state: str | None,
    query: str | None,
    taxonomy: str,
    days_ahead: int,
    per_page: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Search SeatGeek /events for upcoming events matching our filters.

    Returns (flattened_rows, raw_events). raw_events are the untouched event
    objects (what a bronze landing would store); rows feed the CSV/preview.
    """

    start = datetime.now(timezone.utc)
    end = start + timedelta(days=days_ahead)
    rows: list[dict[str, Any]] = []
    raw_events: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):  # SeatGeek pages are 1-indexed
        params: dict[str, Any] = {
            "taxonomies.name": taxonomy,          # "concert" = music shows
            "datetime_utc.gte": iso_utc(start),
            "datetime_utc.lte": iso_utc(end),
            "sort": "datetime_utc.asc",
            "per_page": per_page,
            "page": page,
        }
        if city:
            params["venue.city"] = city
        if state:
            params["venue.state"] = state
        if query:
            params["q"] = query

        payload = fetch_json("events", params)
        events = payload.get("events", [])
        raw_events.extend(events)
        rows.extend(flatten_event(event) for event in events)

        meta = payload.get("meta", {})
        total = int(meta.get("total") or 0)
        seen = page * per_page
        if not events or seen >= total:
            break

    return rows, raw_events


def primary_performer(event: dict[str, Any]) -> dict[str, Any]:
    """Pick the headliner: SeatGeek's `primary` performer, else the first."""

    performers = event.get("performers") or []
    for item in performers:
        if item.get("primary"):
            return item
    return performers[0] if performers else {}


def first_genre(performer: dict[str, Any]) -> str | None:
    """Primary genre name of a performer, if any."""

    genres = performer.get("genres") or []
    for item in genres:
        if item.get("primary"):
            return item.get("name")
    return genres[0].get("name") if genres else None


def flatten_event(event: dict[str, Any]) -> dict[str, Any]:
    """Turn one nested SeatGeek event into one flat row.

    Column names mirror tm_events where the concept matches (venue_*, price_*,
    performer/attraction, date) so a future sg_events silver table joins cleanly
    to Ticketmaster, Trends, and YouTube on artist + venue-DMA + date.
    """

    venue = event.get("venue") or {}
    location = venue.get("location") or {}
    stats = event.get("stats") or {}
    performers = event.get("performers") or []
    head = primary_performer(event)
    taxonomies = event.get("taxonomies") or []
    taxonomy = taxonomies[0].get("name") if taxonomies else None

    return {
        # Repeated runs become historical snapshots (forward-only).
        "extract_ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),

        "event_id": str(event.get("id")) if event.get("id") is not None else None,
        "event_title": event.get("title"),
        "event_type": event.get("type"),
        "taxonomy": taxonomy,
        "event_url": event.get("url"),

        # Timing + open/closed state.
        "datetime_utc": event.get("datetime_utc"),
        "datetime_local": event.get("datetime_local"),
        "visible_until_utc": event.get("visible_until_utc"),
        "announce_date": event.get("announce_date"),
        "is_open": event.get("is_open"),

        # SeatGeek's own demand metrics (its popularity model).
        "sg_score": event.get("score"),
        "sg_popularity": event.get("popularity"),

        # Secondary-market price + inventory stats — the resale-demand signal.
        "listing_count": stats.get("listing_count"),
        "visible_listing_count": stats.get("visible_listing_count"),
        "price_min": stats.get("lowest_price"),
        "price_avg": stats.get("average_price"),
        "price_median": stats.get("median_price"),
        "price_max": stats.get("highest_price"),
        "price_min_good_deals": stats.get("lowest_price_good_deals"),

        # Venue dimension (note: SeatGeek sometimes fills capacity for us).
        "venue_id": str(venue.get("id")) if venue.get("id") is not None else None,
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city"),
        "venue_state_code": venue.get("state"),
        "venue_country_code": venue.get("country"),
        "venue_postal_code": venue.get("postal_code"),
        "venue_latitude": location.get("lat"),
        "venue_longitude": location.get("lon"),
        "venue_capacity": venue.get("capacity"),
        "venue_score": venue.get("score"),

        # Performers/artists (pipe-joined like tm_events attractions).
        "performer_ids": "|".join(str(p.get("id")) for p in performers if p.get("id")),
        "performer_names": "|".join(p.get("name", "") for p in performers if p.get("name")),
        "primary_performer": head.get("name"),
        "primary_performer_genre": first_genre(head),
        "primary_performer_score": head.get("score"),
    }


def write_csv(path: str, rows: list[dict[str, Any]]) -> None:
    """Write the flattened event snapshot to CSV."""

    if not rows:
        print("No rows to write.")
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def print_events(rows: list[dict[str, Any]]) -> None:
    """Print a short preview of event rows."""

    if not rows:
        print("No events found for this filter set.")
        return
    print(f"Fetched {len(rows)} events.")
    for row in rows[:20]:
        print(
            f"- {row['datetime_local'] or row['datetime_utc']} | "
            f"{row['event_title']} | {row['venue_name']} | "
            f"price_min={row['price_min']} | listings={row['listing_count']}"
        )


def print_richness_summary(rows: list[dict[str, Any]]) -> None:
    """Counts that decide whether SeatGeek is richer than Ticketmaster for us.

    The key comparison is price coverage vs Ticketmaster's profiled ~23%, plus
    how often listing_count (resale demand) and venue_capacity are populated.
    """

    if not rows:
        return
    total = len(rows)

    def pct(n: int) -> str:
        return f"{n}/{total} ({round(100 * n / total, 1)}%)"

    with_price = sum(1 for r in rows if r.get("price_min") is not None)
    with_listings = sum(1 for r in rows if (r.get("listing_count") or 0) > 0)
    with_capacity = sum(1 for r in rows if (r.get("venue_capacity") or 0) > 0)
    with_venue = sum(1 for r in rows if r.get("venue_id"))
    with_perf = sum(1 for r in rows if r.get("performer_names"))
    with_genre = sum(1 for r in rows if r.get("primary_performer_genre"))
    open_counts = Counter("open" if r.get("is_open") else "closed" for r in rows)

    with_score = sum(1 for r in rows if r.get("sg_score") is not None)

    print("\nData richness summary:")
    print(f"- events returned: {total}")
    print(f"- with sg_score/popularity (demand model): {pct(with_score)}")
    print(f"- with venue_capacity > 0 (helps dim_venue): {pct(with_capacity)}")
    print(f"- with venue_id: {pct(with_venue)}")
    print(f"- with performers: {pct(with_perf)}")
    print(f"- with genre: {pct(with_genre)}")
    print(f"- with price (lowest_price): {pct(with_price)}")
    print(f"- with listing_count > 0 (resale inventory): {pct(with_listings)}")
    if with_price == 0 and with_listings == 0 and total:
        print("  NOTE: price + listing_count are GATED to SeatGeek partner access; "
              "the basic client_id returns stats={} (verified 2026-06-15).")
    print(f"- open/closed: {dict(open_counts)}")


def parse_args() -> argparse.Namespace:
    """Define command-line options for the POC."""

    parser = argparse.ArgumentParser(description="SeatGeek Platform API POC extractor")
    parser.add_argument("--city", help="Optional venue city filter, e.g. 'San Francisco'")
    parser.add_argument("--state", help="Optional two-letter venue state, e.g. CA")
    parser.add_argument("--query", help="Optional keyword search (q=)")
    parser.add_argument("--taxonomy", default="concert",
                        help="Event taxonomy filter. Default: concert (music shows)")
    parser.add_argument("--days-ahead", type=int, default=180, help="Upcoming event window")
    parser.add_argument("--per-page", type=int, default=100, help="Events per API page")
    parser.add_argument("--max-pages", type=int, default=3, help="Maximum API pages to fetch")
    parser.add_argument("--output", help="Optional CSV output path")
    parser.add_argument(
        "--upload-raw", action="store_true",
        help="Upload the untouched event JSON to the bronze GCS bucket "
        "(raw/seatgeek/dt=<date>/...). Requires google-cloud-storage + ADC.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env()

    rows, raw_events = search_events(
        city=args.city,
        state=args.state,
        query=args.query,
        taxonomy=args.taxonomy,
        days_ahead=args.days_ahead,
        per_page=args.per_page,
        max_pages=args.max_pages,
    )

    print_events(rows)
    print_richness_summary(rows)

    if args.output:
        write_csv(args.output, rows)

    if args.upload_raw:
        if not raw_events:
            print("\nNothing to upload — no raw events captured.")
        else:
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from common.gcs_io import upload_raw

            upload_raw("seatgeek", raw_events)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
