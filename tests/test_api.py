import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from api.app import app  # noqa: E402
from api.gold import GoldFrames, GoldRepository, set_repository  # noqa: E402


def fixture_frames() -> GoldFrames:
    fact = pd.DataFrame(
        [
            {
                "event_id": "event_soon",
                "snapshot_date": "2026-06-24",
                "artist_id": "artist_1",
                "venue_id": "venue_1",
                "dma_code": "807",
                "days_to_show": 10,
                "price_min": 80.0,
                "price_max": 160.0,
                "price_currency": "USD",
                "status_code": "onsale",
                "local_interest": 61,
                "yt_subscribers": 1000,
                "yt_views": 20000,
            },
            {
                "event_id": "event_soon",
                "snapshot_date": "2026-06-25",
                "artist_id": "artist_1",
                "venue_id": "venue_1",
                "dma_code": "807",
                "days_to_show": 9,
                "price_min": 85.0,
                "price_max": 170.0,
                "price_currency": "USD",
                "status_code": "onsale",
                "local_interest": 65,
                "yt_subscribers": 1100,
                "yt_views": 21000,
            },
            {
                "event_id": "event_later",
                "snapshot_date": "2026-06-25",
                "artist_id": "artist_2",
                "venue_id": "venue_2",
                "dma_code": "807",
                "days_to_show": 40,
                "price_min": None,
                "price_max": None,
                "price_currency": "USD",
                "status_code": "onsale",
                "local_interest": pd.NA,
                "yt_subscribers": pd.NA,
                "yt_views": pd.NA,
            },
        ]
    )
    forecast = pd.DataFrame(
        [
            {"event_id": "event_soon", "days_to_show": 9, "predicted_price": 90.0},
            {"event_id": "event_soon", "days_to_show": 0, "predicted_price": 120.0},
            {"event_id": "event_later", "days_to_show": 40, "predicted_price": 50.0},
            {"event_id": "event_later", "days_to_show": 0, "predicted_price": 75.0},
        ]
    )
    dim_event = pd.DataFrame(
        [
            {
                "event_id": "event_soon",
                "event_name": "Soon Show",
                "show_date": "2026-07-04",
                "event_url": "https://example.com/soon",
                "venue_id": "venue_1",
                "primary_genre": "Dance/Electronic",
            },
            {
                "event_id": "event_later",
                "event_name": "Later Show",
                "show_date": "2026-08-04",
                "event_url": "https://example.com/later",
                "venue_id": "venue_2",
                "primary_genre": "Dance/Electronic",
            },
        ]
    )
    dim_venue = pd.DataFrame(
        [
            {
                "venue_id": "venue_1",
                "venue_name": "Greek Theatre",
                "city": "Berkeley",
                "state_code": "CA",
                "dma_code": "807",
                "capacity": 8500,
            },
            {
                "venue_id": "venue_2",
                "venue_name": "Bill Graham Civic Auditorium",
                "city": "San Francisco",
                "state_code": "CA",
                "dma_code": "807",
                "capacity": 8500,
            },
        ]
    )
    dim_artist = pd.DataFrame(
        [
            {"artist_id": "artist_1", "artist_name": "Artist One"},
            {"artist_id": "artist_2", "artist_name": "Artist Two"},
        ]
    )
    return GoldFrames(fact, forecast, dim_event, dim_venue, dim_artist)


@pytest.fixture(autouse=True)
def injected_repository():
    set_repository(GoldRepository(fixture_frames()))
    yield
    set_repository(None)


@pytest.fixture
def client():
    return TestClient(app)


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_shows_returns_latest_summaries_sorted_by_show_date(client):
    response = client.get("/shows")

    assert response.status_code == 200
    shows = response.json()
    assert len(shows) == 2
    assert [show["event_id"] for show in shows] == ["event_soon", "event_later"]
    assert shows[0]["event_name"] == "Soon Show"
    assert shows[0]["artist_name"] == "Artist One"
    assert shows[0]["venue_name"] == "Greek Theatre"
    assert shows[0]["price_min"] == 85.0
    assert shows[0]["forecast_price"] == 120.0
    assert shows[1]["local_interest"] is None
    assert shows[1]["yt_subscribers"] is None


def test_show_returns_summary_history_and_forecast(client):
    response = client.get("/show/event_soon")

    assert response.status_code == 200
    show = response.json()
    assert show["event_id"] == "event_soon"
    assert show["forecast_price"] == 120.0
    assert [point["snapshot_date"] for point in show["history"]] == [
        "2026-06-24T00:00:00",
        "2026-06-25T00:00:00",
    ]
    assert [point["days_to_show"] for point in show["forecast"]] == [9, 0]
    assert [point["predicted_price"] for point in show["forecast"]] == [90.0, 120.0]


def test_show_serializes_missing_signals_as_null(client):
    response = client.get("/show/event_later")

    assert response.status_code == 200
    show = response.json()
    assert show["local_interest"] is None
    assert show["yt_subscribers"] is None
    assert show["history"][0]["local_interest"] is None


def test_show_returns_404_for_missing_event(client):
    response = client.get("/show/does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"] == "Show not found: does-not-exist"
