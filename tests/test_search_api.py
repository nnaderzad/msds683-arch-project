"""Offline tests for the /search and /genres endpoints (in-memory gold frames)."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.app import app
from api.gold import GoldFrames, GoldRepository, set_repository

TODAY = date.today()


def frames() -> GoldFrames:
    fact = pd.DataFrame(
        [
            # Bay Area EDM show next week, cheap, with forecast
            {"event_id": "edm1", "snapshot_date": "2026-08-07", "artist_id": 1,
             "venue_id": 10, "dma_code": "807", "days_to_show": 7,
             "price_min": 30.0, "price_max": 60.0, "status_code": "onsale",
             "local_interest": 80, "yt_subscribers": 5000, "yt_views": None},
            # NY rock show far out, expensive, no forecast row
            {"event_id": "rock1", "snapshot_date": "2026-08-07", "artist_id": 2,
             "venue_id": 20, "dma_code": "501", "days_to_show": 60,
             "price_min": 120.0, "price_max": 250.0, "status_code": "onsale",
             "local_interest": None, "yt_subscribers": None, "yt_views": None},
            # Past Bay Area show (must be excluded by days_ahead)
            {"event_id": "past1", "snapshot_date": "2026-07-01", "artist_id": 1,
             "venue_id": 10, "dma_code": "807", "days_to_show": -5,
             "price_min": 25.0, "price_max": 40.0, "status_code": "offsale",
             "local_interest": 10, "yt_subscribers": 5000, "yt_views": None},
        ]
    )
    dim_event = pd.DataFrame(
        [
            {"event_id": "edm1", "event_name": "Warehouse Rave", "venue_id": 10,
             "show_date": (TODAY + timedelta(days=7)).isoformat(),
             "primary_genre": "Dance/Electronic"},
            {"event_id": "rock1", "event_name": "Stadium Anthems", "venue_id": 20,
             "show_date": (TODAY + timedelta(days=60)).isoformat(),
             "primary_genre": "Rock"},
            {"event_id": "past1", "event_name": "Bygone Beats", "venue_id": 10,
             "show_date": (TODAY - timedelta(days=5)).isoformat(),
             "primary_genre": "Dance/Electronic"},
        ]
    )
    dim_venue = pd.DataFrame(
        [
            {"venue_id": 10, "venue_name": "Public Works", "city": "San Francisco",
             "state_code": "CA"},
            {"venue_id": 20, "venue_name": "Big Arena", "city": "New York",
             "state_code": "NY"},
        ]
    )
    dim_artist = pd.DataFrame(
        [
            {"artist_id": 1, "artist_name": "DJ Fixture"},
            {"artist_id": 2, "artist_name": "The Loud Ones"},
        ]
    )
    forecast = pd.DataFrame(
        [
            {"event_id": "edm1", "days_to_show": 7, "predicted_price": 42.0},
            {"event_id": "edm1", "days_to_show": 0, "predicted_price": 45.0},
        ]
    )
    return GoldFrames(fact, forecast, dim_event, dim_venue, dim_artist)


@pytest.fixture(autouse=True)
def gold_repo():
    set_repository(GoldRepository(frames()))
    yield
    set_repository(None)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_genres_sorted_distinct(client):
    assert client.get("/genres").json() == ["Dance/Electronic", "Rock"]


def test_search_no_filters_returns_everything(client):
    rows = client.get("/search").json()
    assert {r["event_id"] for r in rows} == {"edm1", "rock1", "past1"}
    assert rows[0]["primary_genre"] is not None  # summaries now carry genre + dma


def test_search_compound_edm_bay_area_under_50(client):
    rows = client.get(
        "/search",
        params={"genre": "Dance/Electronic", "dma": "807", "max_price": 50,
                "days_ahead": 14},
    ).json()
    assert [r["event_id"] for r in rows] == ["edm1"]
    # nearest-to-show forecast (45.0) is the effective price, under the cap
    assert rows[0]["forecast_price"] == 45.0


def test_search_days_ahead_excludes_past(client):
    rows = client.get("/search", params={"days_ahead": 365}).json()
    assert {r["event_id"] for r in rows} == {"edm1", "rock1"}


def test_search_text_matches_venue_and_artist(client):
    by_venue = client.get("/search", params={"q": "public works"}).json()
    assert {r["event_id"] for r in by_venue} == {"edm1", "past1"}
    by_artist = client.get("/search", params={"q": "loud ones"}).json()
    assert [r["event_id"] for r in by_artist] == ["rock1"]


def test_search_state_filter(client):
    rows = client.get("/search", params={"state": "ny"}).json()
    assert [r["event_id"] for r in rows] == ["rock1"]


def test_search_max_price_falls_back_to_observed_min(client):
    # rock1 has no forecast row -> effective price is price_min (120)
    rows = client.get("/search", params={"max_price": 130}).json()
    assert {r["event_id"] for r in rows} == {"edm1", "rock1", "past1"}
    rows = client.get("/search", params={"max_price": 100}).json()
    assert {r["event_id"] for r in rows} == {"edm1", "past1"}


def test_search_rejects_bad_params(client):
    assert client.get("/search", params={"state": "CALI"}).status_code == 422
    assert client.get("/search", params={"days_ahead": 9999}).status_code == 422
