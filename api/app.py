"""FastAPI skeleton for the event demand app.

E1 intentionally uses stubbed records. E2 will keep the endpoint contract and
replace the in-memory data with BigQuery reads from the gold layer.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

app = FastAPI(
    title="Event Demand API",
    description="Skeleton API for serving show-level demand signals from the gold layer.",
    version="0.1.0",
)


STUB_SHOWS: list[dict[str, Any]] = [
    {
        "event_id": "demo_001",
        "event_name": "Fred again..",
        "artist_name": "Fred again..",
        "venue_name": "Greek Theatre",
        "city": "Berkeley",
        "state_code": "CA",
        "show_date": "2026-09-12",
        "price_min": 85.0,
        "price_max": 220.0,
        "status_code": "onsale",
        "local_interest": 74,
        "youtube_subscribers": 1_200_000,
        "youtube_views": 415_000_000,
        "forecast_price": 145.0,
    },
    {
        "event_id": "demo_002",
        "event_name": "Disclosure",
        "artist_name": "Disclosure",
        "venue_name": "Bill Graham Civic Auditorium",
        "city": "San Francisco",
        "state_code": "CA",
        "show_date": "2026-10-03",
        "price_min": 62.5,
        "price_max": 180.0,
        "status_code": "onsale",
        "local_interest": 58,
        "youtube_subscribers": 920_000,
        "youtube_views": 610_000_000,
        "forecast_price": 118.0,
    },
]


@app.get("/health")
def health() -> dict[str, str]:
    """Return a simple health check for local and deployed smoke tests."""
    return {"status": "ok"}


@app.get("/shows")
def list_shows() -> list[dict[str, Any]]:
    """Return show summaries for the frontend dropdown."""
    return STUB_SHOWS


@app.get("/show/{event_id}")
def get_show(event_id: str) -> dict[str, Any]:
    """Return one show by event id."""
    for show in STUB_SHOWS:
        if show["event_id"] == event_id:
            return show

    raise HTTPException(status_code=404, detail=f"Show not found: {event_id}")
