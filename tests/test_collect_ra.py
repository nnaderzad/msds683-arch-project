"""Offline tests for the RA collector's response parsing (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ra_api"))

from collect_ra import rows_from_listings, summarize  # noqa: E402

RESPONSE = {
    "data": {
        "eventListings": {
            "totalResults": 214,
            "data": [
                {"event": {
                    "id": "2441944", "title": "Kausmic Campout",
                    "date": "2026-07-03T00:00:00.000", "startTime": "2026-07-03T22:00:00.000",
                    "endTime": "2026-07-04T04:00:00.000", "attending": 87,
                    "isTicketed": True, "cost": "$183", "contentUrl": "/events/2441944",
                    "venue": {"id": "5031", "name": "The Midway"},
                    "artists": [{"id": "1", "name": "Berry B"}, {"id": "2", "name": "Cwizz"}],
                    "genres": [{"name": "Tech House"}],
                }},
                {"event": {
                    "id": "2441999", "title": "Free Club Night", "date": "2026-07-05",
                    "startTime": None, "endTime": None, "attending": 0,
                    "isTicketed": False, "cost": None, "contentUrl": None,
                    "venue": None, "artists": [], "genres": [],
                }},
            ],
        }
    }
}


def test_rows_from_listings_flattens_events():
    rows = rows_from_listings(RESPONSE, "t0")
    assert len(rows) == 2
    r = rows[0]
    assert r["ra_event_id"] == "2441944" and r["event_date"] == "2026-07-03"
    assert r["venue"] == "The Midway" and r["artists"] == "Berry B|Cwizz"
    assert r["attending"] == 87 and r["cost_text"] == "$183" and r["is_ticketed"] is True
    assert r["event_url"] == "https://ra.co/events/2441944"
    # sparse event stays a valid row
    r2 = rows[1]
    assert r2["venue"] is None and r2["artists"] is None and r2["event_url"] is None


def test_summarize_counts():
    rows = rows_from_listings(RESPONSE, "t0")
    s = summarize(rows, 214)
    assert s["events_returned"] == 2 and s["total_results_reported"] == 214
    assert s["pct_with_cost"] == 50.0 and s["pct_ticketed"] == 50.0
    assert s["attending_total"] == 87
    assert s["date_min"] == "2026-07-03" and s["date_max"] == "2026-07-05"


def test_empty_response_is_safe():
    assert rows_from_listings({"data": {"eventListings": None}}, "t0") == []
    assert summarize([], None)["events_returned"] == 0
