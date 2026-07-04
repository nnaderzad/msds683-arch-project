"""Offline tests for the ticket-page JSON-LD offer extraction (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nineteenhz_api"))

from poll_ticket_pages import event_ld_blocks, rows_from_event_ld  # noqa: E402

EVENTBRITE_STYLE = {
    "@type": "Festival",
    "name": "Om Getaway",
    "startDate": "2026-07-02T14:00:00-07:00",
    "location": {"@type": "Place", "name": "Deloro Valley"},
    "eventStatus": "https://schema.org/EventScheduled",
    "offers": [{"@type": "AggregateOffer", "lowPrice": "219.03", "highPrice": "427.71",
                "availability": "InStock", "priceCurrency": "USD"}],
}

SHOTGUN_STYLE = {
    "@type": "MusicEvent",
    "name": "Five Finger Disco",
    "startDate": "2026-07-12",
    "location": [{"@type": "Place", "name": "White Horse Inn"}],
    "offers": [
        {"@type": "Offer", "name": "GA", "price": 10, "priceCurrency": "USD",
         "availability": "https://schema.org/InStock"},
        {"@type": "Offer", "name": "Late", "price": 15, "priceCurrency": "USD",
         "availability": "https://schema.org/SoldOut"},
    ],
}


def test_aggregate_offer_row():
    rows = rows_from_event_ld(EVENTBRITE_STYLE, "https://www.eventbrite.com/e/x", "t0")
    assert len(rows) == 1
    r = rows[0]
    assert r["ld_type"] == "Festival" and r["event_name"] == "Om Getaway"
    assert r["start_date"] == "2026-07-02" and r["venue_name"] == "Deloro Valley"
    assert r["event_status"] == "EventScheduled"
    assert (r["price_min"], r["price_max"]) == ("219.03", "427.71")
    assert r["availability"] == "InStock" and r["ticket_domain"] == "eventbrite.com"


def test_per_tier_offers_and_soldout():
    rows = rows_from_event_ld(SHOTGUN_STYLE, "https://shotgun.live/en/events/x", "t0")
    assert [(r["offer_name"], r["price_min"], r["availability"]) for r in rows] == [
        ("GA", 10, "InStock"), ("Late", 15, "SoldOut")]
    assert rows[0]["venue_name"] == "White Horse Inn"  # location-as-list handled


def test_offerless_event_still_emits_one_row():
    item = {"@type": "DanceEvent", "name": "Club Night", "startDate": "2026-08-01"}
    rows = rows_from_event_ld(item, "https://example.com/e", "t0")
    assert len(rows) == 1 and rows[0]["price_min"] is None


def test_event_ld_blocks_filters_non_events():
    html = (
        '<script type="application/ld+json">{"@type": "WebSite", "url": "x"}</script>'
        '<script type="application/ld+json">[{"@type": "MusicEvent", "name": "A"}]</script>'
        '<script type="application/ld+json">not json</script>'
    )
    items = event_ld_blocks(html)
    assert [i["@type"] for i in items] == ["MusicEvent"]
