#!/usr/bin/env python3
"""19hz.info Bay Area listing collector — face prices + full lineups for club shows.

19hz.info is the canonical Bay Area electronic-music listing. Unlike Ticketmaster
it covers the club/warehouse scene (Dice, Tixr, Eventbrite, RA, Posh ticket links)
and publishes a **Price | Age** column — a face-value price source for exactly the
shows where TM priceRanges is structurally empty (docs/collection_efficiency_review.md,
finding 11). One polite fetch per run; re-running daily builds a price time-series
in bronze the same way the other sources do.

What it does:
  1. GET the Bay Area listing page (one request).
  2. Optionally land the UNTOUCHED HTML in bronze:
       gs://<raw>/nineteenhz/dt=<date>/nineteenhz_bayarea_<stamp>.html
  3. Parse the events table into tidy long-format rows (one row per event) with
     ``extract_ts_utc``, and write a CSV.
  4. Print a data-richness summary (events, % priced, % with parsed lineup,
     ticket-link domains) like the other source POCs.

Parsing notes (deterministic, no LLM): the site's HTML nests the genre tags as an
unclosed ``<td>`` inside the title cell, so cells are split manually; the hidden
``<div class='shrink'>YYYY/MM/DD</div>`` cell carries the machine-readable start
date. Artists are derived from the title's lineup conventions ("Party: A, B b2b C")
— kept both raw and exploded so the join layer can choose.

Standard library only (no pip installs); ``--land-raw`` needs google-cloud-storage
(same as the other collectors' bronze landing).

Run (repo root):
    python nineteenhz_api/collect_19hz.py --dry-run                 # fetch + summary
    python nineteenhz_api/collect_19hz.py --output data/nineteenhz/events.csv
    python nineteenhz_api/collect_19hz.py --output ... --land-raw   # also land bronze HTML
"""

from __future__ import annotations

import argparse
import csv
import html as htmllib
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

LISTING_URL = "https://19hz.info/eventlisting_BayArea.php"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"  # plain requests 403
SOURCE = "nineteenhz"

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>", re.S)
_LINK = re.compile(r"<a href='([^']+)'>(.*?)</a>", re.S)
_HIDDEN_DATE = re.compile(r"<div class='shrink'>(\d{4}/\d{2}/\d{2})</div>")
_TAG = re.compile(r"<[^>]+>")
_PRICE = re.compile(r"\$(\d+(?:\.\d{1,2})?)(?:\s*[-–]\s*\$?(\d+(?:\.\d{1,2})?))?(\+)?")
_VENUE = re.compile(r"@\s*(.+?)\s*(?:\(([^)]+)\))?\s*$")


def _clean(fragment: str) -> str:
    """Strip tags + entities from an HTML fragment."""
    return htmllib.unescape(_TAG.sub(" ", fragment)).replace("\xa0", " ").strip(" \n\t|")


def split_cells(row_html: str) -> list[str]:
    """Split a <tr> body on <td> boundaries (the site leaves them unclosed)."""
    parts = _CELL.split(row_html)
    return [p.replace("</td>", "") for p in parts[1:]] if len(parts) > 1 else []


def parse_price(price_cell: str) -> dict:
    """'$68-80 | 21+' / '$183+ | 21+' / 'free | All ages' -> structured price."""
    text = _clean(price_cell)
    price_part, _, age_part = (p.strip() for p in text.partition("|"))
    out = {"price_text": price_part or None, "age_restriction": age_part or None,
           "is_free": "free" in price_part.lower(), "price_min": None, "price_max": None,
           "price_open_ended": False}
    m = _PRICE.search(price_part)
    if m:
        out["price_min"] = float(m.group(1))
        out["price_max"] = float(m.group(2)) if m.group(2) else float(m.group(1))
        out["price_open_ended"] = bool(m.group(3))
    if out["is_free"]:
        out["price_min"], out["price_max"] = 0.0, 0.0
    return out


def parse_artists(title: str) -> list[str]:
    """Lineup from title conventions: 'Party: A, B b2b C' -> [A, B, C]."""
    lineup = title.rsplit(": ", 1)[-1] if ": " in title else title
    names: list[str] = []
    for chunk in lineup.split(","):
        for name in re.split(r"\s+b2b\s+", chunk, flags=re.I):
            name = name.strip(" .")
            if name and not re.fullmatch(r"(and\s+)?(more|guests?|tba)\b.*", name, re.I):
                names.append(name)
    return names


def parse_row(row_html: str) -> dict | None:
    """One <tr> -> one tidy event record (None for headers/malformed rows)."""
    m = _HIDDEN_DATE.search(row_html)
    if not m:
        return None
    cells = split_cells(row_html)
    if len(cells) < 3:
        return None
    datetime_text = _clean(cells[0])

    # cells[1] = "<a href=URL>TITLE</a> @ VENUE (CITY)" + unclosed "<td>tags"
    title_cell, genre_cell = cells[1], cells[2] if len(cells) > 3 else ""
    link = _LINK.search(title_cell)
    ticket_url = link.group(1) if link else None
    title = _clean(link.group(2)) if link else None
    after_link = title_cell[link.end():] if link else title_cell
    vm = _VENUE.search(_clean(after_link))
    venue = vm.group(1) if vm else None
    city = vm.group(2) if vm and vm.group(2) else None

    # remaining cells shift by one because of the nested td: [3]=price, [4]=organizers
    price = parse_price(cells[3] if len(cells) > 3 else "")
    organizers = _clean(cells[4]) if len(cells) > 4 else None
    artists = parse_artists(title or "")
    return {
        "event_date": m.group(1).replace("/", "-"),
        "datetime_text": datetime_text,
        "title": title,
        "venue": venue,
        "city": city,
        "genres": _clean(genre_cell) or None,
        **price,
        "organizers": organizers or None,
        "artists": "|".join(artists) or None,
        "n_artists": len(artists),
        "ticket_url": ticket_url,
        "ticket_domain": urlparse(ticket_url).netloc.removeprefix("www.") if ticket_url else None,
    }


def parse_listing(page_html: str) -> list[dict]:
    rows = [parse_row(r) for r in _ROW.findall(page_html)]
    return [r for r in rows if r]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def summarize(events: list[dict]) -> dict:
    n = len(events)
    pct = lambda k: round(100 * sum(bool(e[k]) for e in events) / n, 1) if n else 0.0  # noqa: E731
    domains: dict[str, int] = {}
    for e in events:
        if e["ticket_domain"]:
            domains[e["ticket_domain"]] = domains.get(e["ticket_domain"], 0) + 1
    return {
        "events": n,
        "date_min": min((e["event_date"] for e in events), default=None),
        "date_max": max((e["event_date"] for e in events), default=None),
        "pct_with_price": round(100 * sum(e["price_min"] is not None for e in events) / n, 1) if n else 0.0,
        "pct_free": pct("is_free"),
        "pct_with_artists": pct("artists"),
        "pct_with_venue": pct("venue"),
        "ticket_domains": dict(sorted(domains.items(), key=lambda kv: -kv[1])[:8]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=LISTING_URL)
    parser.add_argument("--output", default=None, help="CSV path (omit for dry run)")
    parser.add_argument("--land-raw", action="store_true",
                        help="also land the untouched HTML in the bronze bucket")
    parser.add_argument("--dry-run", action="store_true", help="fetch + summarize only")
    args = parser.parse_args()

    extract_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    page = fetch(args.url)
    events = parse_listing(page)
    for e in events:
        e["extract_ts_utc"] = extract_ts

    print(f"[collect_19hz] {extract_ts} {summarize(events)}")

    if args.land_raw:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from common import gcs_io
        uri = gcs_io.upload_raw(SOURCE, page, ext="html", suffix="bayarea")
        print(f"[collect_19hz] landed raw HTML -> {uri}")

    if args.output and not args.dry_run:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(events[0].keys()))
            writer.writeheader()
            writer.writerows(events)
        print(f"[collect_19hz] wrote {len(events)} rows -> {out}")
    elif not args.output:
        print("[collect_19hz] dry run: no --output given, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
