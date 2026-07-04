#!/usr/bin/env python3
"""Daily ticket-page JSON-LD poller — per-event price/availability for club shows.

Companion to ``collect_19hz.py``: takes the ticket URLs discovered on 19hz.info
and reads each event page's embedded **schema.org JSON-LD** (machine-readable
metadata the platforms publish for search engines): offer prices, currency, and
availability/sold-out status. Re-run daily to build a per-event face-price +
sold-out time-series for exactly the club shows Ticketmaster never prices
(docs/collection_efficiency_review.md, decision D6 step 2).

Empirically supported domains (probed 2026-07-04):
  eventbrite.com  AggregateOffer (lowPrice/highPrice + availability)   ~40 events
  shotgun.live    per-tier Offer list (price + availability)           ~5 events
Blocked / not emitting event JSON-LD (kept OUT of the default allowlist):
  tixr.com, ra.co (bot-wall 403), dice.fm 19hz links are partner redirects that
  land on the homepage, posh.vip / instagram (no JSON-LD). Revisit in backlog.

Politeness: one GET per tracked event per run, fixed --sleep between requests
(default 3s), only domains in the allowlist are fetched. Standard library only.

Run (repo root):
    python nineteenhz_api/collect_19hz.py --output data/nineteenhz/events.csv
    python nineteenhz_api/poll_ticket_pages.py --events data/nineteenhz/events.csv \
        --output data/nineteenhz/offers.csv
    python nineteenhz_api/poll_ticket_pages.py --events ... --limit 5 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

SOURCE = "ticketpages"
DEFAULT_DOMAINS = ["eventbrite.com", "shotgun.live"]
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_LDJSON = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_EVENTISH = re.compile(r"Event|Festival", re.I)  # matches Event + all subtypes we care about


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def event_ld_blocks(page_html: str) -> list[dict]:
    """All schema.org items on the page whose @type looks like an Event."""
    out = []
    for block in _LDJSON.findall(page_html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and _EVENTISH.search(str(item.get("@type", ""))):
                out.append(item)
    return out


def _offer_rows(offers, base: dict) -> list[dict]:
    """Normalize Offer / AggregateOffer / list-of-Offer into flat rows."""
    rows = []
    for off in offers if isinstance(offers, list) else [offers]:
        if not isinstance(off, dict):
            continue
        common = {**base,
                  "offer_type": off.get("@type"),
                  "offer_name": off.get("name"),
                  "availability": str(off.get("availability") or "").rsplit("/", 1)[-1] or None,
                  "currency": off.get("priceCurrency"),
                  "valid_from": off.get("validFrom")}
        if off.get("@type") == "AggregateOffer":
            rows.append({**common,
                         "price_min": off.get("lowPrice"), "price_max": off.get("highPrice")})
        else:
            rows.append({**common, "price_min": off.get("price"), "price_max": off.get("price")})
    return rows


def rows_from_event_ld(item: dict, ticket_url: str, extract_ts: str) -> list[dict]:
    """One JSON-LD Event -> one row per offer (or one offer-less row)."""
    location = item.get("location") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    base = {
        "extract_ts_utc": extract_ts,
        "ticket_url": ticket_url,
        "ticket_domain": urlparse(ticket_url).netloc.removeprefix("www."),
        "ld_type": str(item.get("@type")),
        "event_name": item.get("name"),
        "start_date": str(item.get("startDate") or "")[:10] or None,
        "venue_name": (location.get("name") if isinstance(location, dict) else None),
        "event_status": str(item.get("eventStatus") or "").rsplit("/", 1)[-1] or None,
    }
    offers = item.get("offers")
    return _offer_rows(offers, base) if offers else [
        {**base, "offer_type": None, "offer_name": None, "availability": None,
         "currency": None, "valid_from": None, "price_min": None, "price_max": None}]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", required=True,
                        help="CSV from collect_19hz.py (needs ticket_url/ticket_domain)")
    parser.add_argument("--output", default=None, help="offers CSV path (omit for dry run)")
    parser.add_argument("--domains", nargs="*", default=DEFAULT_DOMAINS,
                        help="ticket domains to poll (allowlist)")
    parser.add_argument("--sleep", type=float, default=3.0, help="seconds between requests")
    parser.add_argument("--limit", type=int, default=0, help="cap tracked events (smoke test)")
    parser.add_argument("--land-raw", action="store_true",
                        help="land the extracted JSON-LD payloads in the bronze bucket")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.events, encoding="utf-8") as fh:
        events = [r for r in csv.DictReader(fh)
                  if r.get("ticket_domain") in set(args.domains) and r.get("ticket_url")]
    # one fetch per distinct URL; deterministic order
    urls = sorted({e["ticket_url"] for e in events})
    if args.limit:
        urls = urls[: args.limit]

    extract_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, raw_items, errors = [], [], {}
    for i, url in enumerate(urls):
        if i:
            time.sleep(args.sleep)
        try:
            items = event_ld_blocks(fetch(url))
            raw_items.append({"ticket_url": url, "extract_ts_utc": extract_ts,
                              "event_ld": items})
            for item in items:
                rows.extend(rows_from_event_ld(item, url, extract_ts))
        except Exception as exc:  # isolate per-URL failures, keep polling
            errors[url] = f"{type(exc).__name__}: {exc}"

    with_price = sum(1 for r in rows if r["price_min"] is not None)
    print(f"[poll_ticket_pages] {extract_ts} urls={len(urls)} offer_rows={len(rows)} "
          f"rows_with_price={with_price} errors={len(errors)}")
    for url, err in list(errors.items())[:5]:
        print(f"  error: {url[:70]} -> {err[:80]}")

    if args.land_raw and raw_items:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from common import gcs_io
        uri = gcs_io.upload_raw(SOURCE, raw_items, suffix="jsonld")
        print(f"[poll_ticket_pages] landed raw JSON-LD -> {uri}")

    if args.output and not args.dry_run and rows:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"[poll_ticket_pages] wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
