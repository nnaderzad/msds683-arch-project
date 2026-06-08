#!/usr/bin/env python3
"""Ticketmaster Discovery API proof-of-concept extractor for Milestone 1.

Usage:
  # First, put your Ticketmaster API key into this terminal session.
  # Replace "your_api_key" with the real key from the Ticketmaster developer portal.
  export TICKETMASTER_API_KEY="your_api_key"

  # Example 1: fetch upcoming music events in San Francisco.
  # San Francisco is only an example city, not a hardcoded project limit.
  python ticketmaster_api/ticketmaster_poc.py --city "San Francisco" --state-code CA --output sf_events.csv

  # Example 2: search more broadly with a keyword.
  # The keyword can be a venue, artist, or event-related term.
  python ticketmaster_api/ticketmaster_poc.py --keyword "Greek Theatre" --state-code CA

The script uses only the Python standard library so it can run in a fresh
environment without installing packages.
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


# Official Ticketmaster Discovery API v2 root URL from Ticketmaster's docs.
# We add endpoint files like events.json and venues.json to this base URL.
BASE_URL = "https://app.ticketmaster.com/discovery/v2"


def load_env() -> None:
    """Load KEY=VALUE pairs from ticketmaster_api/.env into os.environ.

    Lets the API key live in a git-ignored .env (same pattern as the other
    source POCs) instead of being exported manually each session.
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


def get_api_key() -> str:
    """Read the Ticketmaster API key from an environment variable."""

    # Ticketmaster's docs say the API key is passed as the apikey query parameter.
    api_key = os.getenv("TICKETMASTER_API_KEY")
    if not api_key:
        raise SystemExit(
            "Missing TICKETMASTER_API_KEY. Create a Ticketmaster developer app, "
            "then run: export TICKETMASTER_API_KEY='your_api_key'"
        )
    return api_key


def fetch_json(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Call one Ticketmaster endpoint and return its JSON response."""

    # Every request must include apikey. The rest of params are filters like
    # city, stateCode, classificationName, startDateTime, and endDateTime.
    query_params = {"apikey": get_api_key(), **params}

    # Full example URL shape:
    # https://app.ticketmaster.com/discovery/v2/events.json?apikey=...&city=San+Francisco
    url = f"{BASE_URL}/{endpoint}?{urlencode(query_params, doseq=True)}"
    request = Request(url, headers={"User-Agent": "msds-data-architecture-poc/1.0"})
    ssl_context = build_ssl_context()

    try:
        with urlopen(request, timeout=30, context=ssl_context) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Ticketmaster HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"Could not reach Ticketmaster API: {exc.reason}") from exc


def build_ssl_context() -> ssl.SSLContext:
    """Use certifi's certificate bundle when available.

    Some local Anaconda installs do not expose the right CA certificate path to
    urllib by default. certifi gives urllib an explicit trusted CA bundle without
    disabling HTTPS verification.
    """

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()

    return ssl.create_default_context(cafile=certifi.where())


def iso_utc(dt: datetime) -> str:
    """Format datetimes the way Ticketmaster expects: YYYY-MM-DDTHH:MM:SSZ."""

    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def search_events(
    city: str | None,
    state_code: str,
    keyword: str | None,
    venue_id: str | None,
    classification_name: str,
    days_ahead: int,
    size: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Search Ticketmaster /events for upcoming events matching our filters.

    Returns (flattened_rows, raw_events). raw_events holds the untouched event
    objects exactly as Ticketmaster returned them — that is what we land in the
    bronze bucket, while the flattened rows feed the CSV/preview.
    """

    # We snapshot from now through N days ahead. Re-running later lets us track
    # changes in event status, price ranges, and availability-related metadata.
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=days_ahead)
    rows: list[dict[str, Any]] = []
    raw_events: list[dict[str, Any]] = []

    for page in range(max_pages):
        # This calls GET /discovery/v2/events.json.
        # For our project, music events in CA are a good first live test.
        # city, keyword, and venueId are optional so the script can test many scopes.
        params: dict[str, Any] = {
            "countryCode": "US",
            "stateCode": state_code,
            "classificationName": classification_name,
            "startDateTime": iso_utc(start),
            "endDateTime": iso_utc(end),
            "sort": "date,asc",
            "size": size,
            "page": page,
            "includeTBA": "no",
            "includeTBD": "no",
        }
        if city:
            params["city"] = city
        if keyword:
            params["keyword"] = keyword
        if venue_id:
            params["venueId"] = venue_id

        payload = fetch_json("events.json", params)
        events = payload.get("_embedded", {}).get("events", [])
        raw_events.extend(events)  # keep the untouched objects for the bronze layer
        rows.extend(flatten_event(event) for event in events)

        # Ticketmaster paginates responses with a page object.
        # Stop once there are no more pages or we hit the requested page limit.
        page_info = payload.get("page", {})
        current_page = int(page_info.get("number") or page)
        total_pages = int(page_info.get("totalPages") or 0)
        if not events or current_page + 1 >= total_pages:
            break

    return rows, raw_events


def first_price_range(event: dict[str, Any]) -> dict[str, Any]:
    """Pick one price range from the event, preferring standard prices."""

    # Ticketmaster priceRanges can be missing. When present, it usually includes
    # min/max/currency. This is not the same as live resale listing count.
    ranges = event.get("priceRanges") or []
    for item in ranges:
        if item.get("type") == "standard":
            return item
    return ranges[0] if ranges else {}


def primary_classification(event: dict[str, Any]) -> dict[str, Any]:
    """Pick the primary classification if Ticketmaster marks one."""

    classifications = event.get("classifications") or []
    for item in classifications:
        if item.get("primary"):
            return item
    return classifications[0] if classifications else {}


def flatten_event(event: dict[str, Any]) -> dict[str, Any]:
    """Turn one nested Ticketmaster event JSON object into one flat row."""

    embedded = event.get("_embedded") or {}

    # Ticketmaster usually nests venue and artist/attraction data under _embedded.
    venues = embedded.get("venues") or []
    attractions = embedded.get("attractions") or []
    venue = venues[0] if venues else {}

    dates = event.get("dates") or {}
    start = dates.get("start") or {}
    status = dates.get("status") or {}
    sales_public = (event.get("sales") or {}).get("public") or {}
    price_range = first_price_range(event)
    classification = primary_classification(event)

    segment = classification.get("segment") or {}
    genre = classification.get("genre") or {}
    subgenre = classification.get("subGenre") or {}

    city = venue.get("city") or {}
    state = venue.get("state") or {}
    country = venue.get("country") or {}
    location = venue.get("location") or {}
    address = venue.get("address") or {}

    return {
        # This timestamp lets repeated script runs become historical snapshots.
        "extract_ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),

        # Event identity and URL fields.
        "event_id": event.get("id"),
        "event_name": event.get("name"),
        "event_type": event.get("type"),
        "event_url": event.get("url"),

        # Event timing and status fields.
        "local_date": start.get("localDate"),
        "local_time": start.get("localTime"),
        "date_time_utc": start.get("dateTime"),
        "timezone": dates.get("timezone"),
        "status_code": status.get("code"),

        # Public onsale window. Useful for demand analysis around sale timing.
        "public_sale_start_utc": sales_public.get("startDateTime"),
        "public_sale_end_utc": sales_public.get("endDateTime"),
        "public_sale_start_tbd": sales_public.get("startTBD"),

        # Price range fields. These may be null, so our live test should measure coverage.
        "price_type": price_range.get("type"),
        "price_currency": price_range.get("currency"),
        "price_min": price_range.get("min"),
        "price_max": price_range.get("max"),

        # Venue fields can become a venue dimension table.
        "venue_id": venue.get("id"),
        "venue_name": venue.get("name"),
        "venue_city": city.get("name"),
        "venue_state_code": state.get("stateCode"),
        "venue_country_code": country.get("countryCode"),
        "venue_postal_code": venue.get("postalCode"),
        "venue_address": address.get("line1"),
        "venue_latitude": location.get("latitude"),
        "venue_longitude": location.get("longitude"),

        # Artists/performers are called attractions in Ticketmaster.
        # Pipe-separated text is fine for this POC; later we would normalize it.
        "attraction_ids": "|".join(str(a.get("id")) for a in attractions if a.get("id")),
        "attraction_names": "|".join(a.get("name", "") for a in attractions if a.get("name")),

        # Classification fields support genre/subgenre analytics.
        "segment": segment.get("name"),
        "genre": genre.get("name"),
        "subgenre": subgenre.get("name"),
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
    """Print a short preview of event rows and key richness checks."""

    if not rows:
        print("No events found for this filter set.")
        return

    print(f"Fetched {len(rows)} events.")
    for row in rows[:20]:
        print(
            f"- {row['local_date']} {row['local_time'] or ''} | "
            f"{row['event_name']} | {row['venue_name']} | "
            f"status={row['status_code']} | "
            f"price={row['price_min']}-{row['price_max']} {row['price_currency'] or ''}"
        )


def print_richness_summary(rows: list[dict[str, Any]]) -> None:
    """Print quick counts that tell us whether this API is useful enough."""

    if not rows:
        return

    total = len(rows)
    with_price = sum(1 for row in rows if row.get("price_min") is not None)
    with_venue = sum(1 for row in rows if row.get("venue_id"))
    with_attractions = sum(1 for row in rows if row.get("attraction_names"))
    with_genre = sum(1 for row in rows if row.get("genre"))
    statuses = Counter(row.get("status_code") or "missing" for row in rows)

    print("\nData richness summary:")
    print(f"- events returned: {total}")
    print(f"- events with venue_id: {with_venue}/{total}")
    print(f"- events with attraction_names: {with_attractions}/{total}")
    print(f"- events with genre: {with_genre}/{total}")
    print(f"- events with price_min: {with_price}/{total}")
    print(f"- status counts: {dict(statuses)}")


def parse_args() -> argparse.Namespace:
    """Define command-line options for the POC."""

    parser = argparse.ArgumentParser(description="Ticketmaster Milestone 1 POC extractor")
    parser.add_argument("--city", help="Optional city filter, e.g. 'San Francisco'")
    parser.add_argument("--state-code", default="CA", help="Two-letter state code, default CA")
    parser.add_argument("--keyword", help="Optional search keyword, e.g. venue or artist name")
    parser.add_argument("--venue-id", help="Optional Ticketmaster venue ID")
    parser.add_argument(
        "--classification-name",
        default="music",
        help="Event classification filter. Default: music",
    )
    parser.add_argument("--days-ahead", type=int, default=180, help="Upcoming event window")
    parser.add_argument("--size", type=int, default=50, help="Events per API page")
    parser.add_argument("--max-pages", type=int, default=3, help="Maximum API pages to fetch")
    parser.add_argument("--output", help="Optional CSV output path")
    parser.add_argument(
        "--upload-raw",
        action="store_true",
        help="Upload the untouched event JSON to the bronze GCS bucket "
        "(raw/ticketmaster/dt=<date>/...). Requires google-cloud-storage + ADC.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env()

    rows, raw_events = search_events(
        city=args.city,
        state_code=args.state_code,
        keyword=args.keyword,
        venue_id=args.venue_id,
        classification_name=args.classification_name,
        days_ahead=args.days_ahead,
        size=args.size,
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
            # Imported lazily so the script still runs (print/CSV) in a bare
            # stdlib environment without google-cloud-storage installed.
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from common.gcs_io import upload_raw

            upload_raw("ticketmaster", raw_events)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
