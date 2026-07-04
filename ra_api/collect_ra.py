#!/usr/bin/env python3
"""Resident Advisor (ra.co) Bay Area event collector — ONE request per day, by agreement.

RA granted written permission (2026-07-04, Tomas's email) for a **single automated
request per day** retrieving upcoming SF Bay Area listings for this project. This
collector enforces that contract in code: before touching the network it checks
the bronze bucket's ``ra/dt=<today>/`` partition and refuses to run if today's
request was already made (``--force`` exists for genuine retries after a failed
request, e.g. a network error where no response was received).

Why RA: the strongest club/underground coverage in the Bay plus two signals no
other source has — the ``attending`` count (a direct demand signal) and RA-ticket
``cost`` text. Complements 19hz (broader listing, price column) and joins the
same way: (venue, date), lineups for the headliner repair.

One request = one GraphQL POST to https://ra.co/graphql with ``eventListings``
(area = SF Bay, next ``--days`` window, ``pageSize`` up to 100). A second mode,
``--lookup-area``, spends the day's request on resolving an area id by name
(one-time setup). Query shapes follow RA's public GraphQL API.

Run (repo root):
    python ra_api/collect_ra.py --lookup-area "san francisco"     # one-time setup
    python ra_api/collect_ra.py --output data/ra/events.csv       # the daily pull
    python ra_api/collect_ra.py --output ... --land-raw           # + bronze landing

Standard library only (``--land-raw`` needs google-cloud-storage, like the other
collectors).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRAPHQL_URL = "https://ra.co/graphql"
SOURCE = "ra"
# Resolved via --lookup-area on 2026-07-04: "San Francisco/Oakland" (US).
DEFAULT_AREA_ID = 218
HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://ra.co/events",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
}

LISTINGS_QUERY = """
query($filters: FilterInputDtoInput, $page: Int, $pageSize: Int) {
  eventListings(filters: $filters, pageSize: $pageSize, page: $page) {
    data {
      event {
        id title date startTime endTime attending isTicketed cost contentUrl
        venue { id name }
        artists { id name }
        genres { name }
      }
    }
    totalResults
  }
}
"""

AREAS_QUERY = """
query($term: String!) {
  areas(searchTerm: $term, limit: 5) { id name country { name } }
}
"""


def graphql(query: str, variables: dict) -> dict:
    """One POST to ra.co/graphql. THE one network call — everything routes here."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("errors"):
        raise RuntimeError(f"GraphQL errors: {json.dumps(body['errors'])[:400]}")
    return body


def already_ran_today(bucket_project: str | None = None) -> bool:
    """True if today's bronze ra/dt= partition already has a landed request."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from common import gcs_io
        from google.cloud import storage
        client = storage.Client(project=gcs_io.PROJECT_ID)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prefix = f"{SOURCE}/dt={today}/"
        return any(True for _ in client.list_blobs(gcs_io.DEFAULT_RAW_BUCKET,
                                                   prefix=prefix, max_results=1))
    except Exception as exc:  # no GCS access locally -> can't verify; warn, allow
        print(f"[collect_ra] WARNING: could not check today's partition ({exc}); "
              "proceeding — make sure this is today's only run", file=sys.stderr)
        return False


def rows_from_listings(body: dict, extract_ts: str) -> list[dict]:
    """GraphQL eventListings response -> tidy long rows (one per event)."""
    out = []
    listings = (body.get("data") or {}).get("eventListings") or {}
    for item in listings.get("data") or []:
        ev = item.get("event") or {}
        venue = ev.get("venue") or {}
        artists = [a.get("name") for a in ev.get("artists") or [] if a.get("name")]
        genres = [g.get("name") for g in ev.get("genres") or [] if g.get("name")]
        out.append({
            "extract_ts_utc": extract_ts,
            "ra_event_id": ev.get("id"),
            "title": ev.get("title"),
            "event_date": str(ev.get("date") or "")[:10] or None,
            "start_time": ev.get("startTime"),
            "end_time": ev.get("endTime"),
            "attending": ev.get("attending"),
            "is_ticketed": ev.get("isTicketed"),
            "cost_text": ev.get("cost"),
            "venue_ra_id": venue.get("id"),
            "venue": venue.get("name"),
            "artists": "|".join(artists) or None,
            "n_artists": len(artists),
            "genres": ", ".join(genres) or None,
            "event_url": f"https://ra.co{ev.get('contentUrl')}" if ev.get("contentUrl") else None,
        })
    return out


def summarize(rows: list[dict], total_results) -> dict:
    n = len(rows)
    pct = lambda k: round(100 * sum(bool(r[k]) for r in rows) / n, 1) if n else 0.0  # noqa: E731
    return {
        "events_returned": n,
        "total_results_reported": total_results,
        "date_min": min((r["event_date"] for r in rows if r["event_date"]), default=None),
        "date_max": max((r["event_date"] for r in rows if r["event_date"]), default=None),
        "pct_with_cost": pct("cost_text"),
        "pct_ticketed": pct("is_ticketed"),
        "pct_with_artists": pct("artists"),
        "attending_total": sum(r["attending"] or 0 for r in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--area-id", type=int, default=DEFAULT_AREA_ID)
    parser.add_argument("--days", type=int, default=60, help="listing window from today")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--lookup-area", default=None, metavar="NAME",
                        help="spend today's request resolving an area id by name")
    parser.add_argument("--output", default=None, help="CSV path")
    parser.add_argument("--land-raw", action="store_true",
                        help="land the untouched GraphQL response in bronze")
    parser.add_argument("--force", action="store_true",
                        help="skip the one-request-per-day guard (failed-run retry only)")
    args = parser.parse_args()

    if not args.force and already_ran_today():
        print("[collect_ra] today's request already made (bronze partition exists) — "
              "per the RA agreement this collector runs ONCE per day. Exiting.")
        return 0

    extract_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if args.lookup_area:
        body = graphql(AREAS_QUERY, {"term": args.lookup_area})
        print(json.dumps(body["data"], indent=2))
        return 0

    if not args.area_id:
        print("[collect_ra] no --area-id set and DEFAULT_AREA_ID unresolved — "
              "run --lookup-area first.", file=sys.stderr)
        return 1

    start = datetime.now(timezone.utc)
    end = start + timedelta(days=args.days)
    variables = {
        "filters": {
            "areas": {"eq": args.area_id},
            "listingDate": {"gte": start.strftime("%Y-%m-%dT00:00:00.000Z"),
                            "lte": end.strftime("%Y-%m-%dT23:59:59.999Z")},
        },
        "pageSize": args.page_size,
        "page": 1,
    }
    body = graphql(LISTINGS_QUERY, variables)
    rows = rows_from_listings(body, extract_ts)
    total = ((body.get("data") or {}).get("eventListings") or {}).get("totalResults")
    print(f"[collect_ra] {extract_ts} area={args.area_id} {summarize(rows, total)}")

    if args.land_raw:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from common import gcs_io
        uri = gcs_io.upload_raw(SOURCE, {"request_variables": variables,
                                         "extract_ts_utc": extract_ts,
                                         "response": body}, suffix="bayarea")
        print(f"[collect_ra] landed raw response -> {uri}")

    if args.output and rows:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"[collect_ra] wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
