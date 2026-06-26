"""Offline tests for A3 — conformed dimensions + bridge (build_dimensions).

Pure builders over the committed seed tm_events fixture (+ small injected roster / YouTube
cache), with PK-uniqueness, FK-coverage, and the headliner name-match rule. No network:
BigQuery, GCS, and the holidays library all live in the I/O layer and are never imported
here (dim_date is tested with an injected holiday set).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))                                   # common.keys
sys.path.insert(0, str(REPO_ROOT / "google_trends_api"))            # geo_lookup
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "silver"))          # build_dimensions

import build_dimensions as d  # noqa: E402
from common import keys  # noqa: E402
from geo_lookup import GeoLookup  # noqa: E402

GEO = GeoLookup()  # reads committed reference CSVs; offline


def _first_weekday(target_isoweekday: int) -> date:
    day = date(2026, 1, 1)
    while day.isoweekday() != target_isoweekday:
        day += timedelta(days=1)
    return day


# ---------------------------------------------------------------------------
# dim_geo + dim_date
# ---------------------------------------------------------------------------

def test_build_dim_geo_renames_columns():
    rows = d.build_dim_geo([{"dma": "807", "dma_name": "San Francisco-Oakland-San Jose CA",
                             "state": "CA", "geo_code": "US-CA-807"}])
    assert rows == [{"dma_code": "807", "geo_code": "US-CA-807",
                     "metro_name": "San Francisco-Oakland-San Jose CA", "state": "CA"}]


def test_date_features_weekend_and_season():
    saturday = d.date_features(_first_weekday(6))
    monday = d.date_features(_first_weekday(1))
    assert saturday["is_weekend"] is True and monday["is_weekend"] is False
    summer = d.date_features(date(2026, 7, 15))
    assert summer["season"] == "Summer" and summer["quarter"] == 3 and summer["month_name"] == "July"


def test_build_dim_date_marks_injected_holiday():
    rows = d.build_dim_date(date(2026, 7, 1), date(2026, 7, 7), {date(2026, 7, 4)})
    assert len(rows) == 7
    assert len({r["date"] for r in rows}) == 7  # date PK unique
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-07-04"]["is_us_holiday"] is True
    assert by_date["2026-07-01"]["is_us_holiday"] is False


# ---------------------------------------------------------------------------
# dim_venue / dim_artist / dim_event over the seed
# ---------------------------------------------------------------------------

def test_dim_venue_resolves_dma_and_has_unique_pk(seed_tm_events):
    venues = d.build_dim_venue(seed_tm_events, GEO)
    assert venues
    ids = [v["venue_id"] for v in venues]
    assert len(ids) == len(set(ids))                      # PK unique
    assert all(v["dma_code"] for v in venues)             # every venue resolves a DMA
    assert any(v["dma_code"] == "807" for v in venues)    # the Bay Area shows
    for v in venues:
        assert v["venue_id"] == keys.venue_id(v["ticketmaster_venue_id"])
        assert v["capacity"] is None                      # backfilled later


def test_dim_artist_enrichment_best_effort(seed_tm_events, seed_artists):
    roster = {keys.normalize_name("Everclear"): {"query": "Everclear band", "top_genre": "Rock"}}
    cache = {keys.normalize_name("Bingo Loco"): "UC_bingo"}
    artists = d.build_dim_artist(seed_tm_events, roster, cache)
    by_norm = {keys.normalize_name(a["artist_name"]): a for a in artists}

    # the 5 seed headliners are all present (plus support acts)
    assert {keys.artist_id(a) for a in seed_artists} <= {a["artist_id"] for a in artists}
    ever = by_norm[keys.normalize_name("Everclear")]
    assert ever["trends_query"] == "Everclear band" and ever["primary_genre"] == "Rock"
    assert by_norm[keys.normalize_name("Bingo Loco")]["yt_channel_id"] == "UC_bingo"
    cain = by_norm[keys.normalize_name("CAIN")]
    assert cain["trends_query"] == "CAIN"          # not in roster -> falls back to name
    assert cain["yt_channel_id"] is None           # not in cache
    assert cain["primary_genre"]                   # from the most-frequent event genre


def test_dim_event_one_row_per_event(seed_tm_events, seed_event_ids):
    events = d.build_dim_event(seed_tm_events)
    assert {e["event_id"] for e in events} == seed_event_ids
    assert len(events) == len(seed_event_ids)      # deduped to current-state
    for e in events:
        assert e["show_date"] and e["venue_id"] is not None


# ---------------------------------------------------------------------------
# Headliner rule + bridge
# ---------------------------------------------------------------------------

def test_pick_headliner_index_name_match_then_first():
    assert d.pick_headliner_index("Everclear with American Hi-Fi",
                                  ["Everclear", "American Hi-Fi"]) == 0
    assert d.pick_headliner_index("Turnover, Narrow Head, She's Green",
                                  ["Turnover", "Narrow Head", "She's Green"]) == 0
    assert d.pick_headliner_index("An Evening with Foo", ["Opener", "Foo"]) == 1
    assert d.pick_headliner_index("Mystery Show", ["Zed", "Bee"]) == 0  # no match -> first


def test_bridge_marks_one_headliner_per_event(seed_tm_events):
    bridge = d.build_bridge_event_artist(seed_tm_events)
    assert bridge
    # exactly one headliner per event that has attractions
    by_event: dict[str, list[dict]] = {}
    for b in bridge:
        by_event.setdefault(b["event_id"], []).append(b)
    for rows in by_event.values():
        assert sum(1 for r in rows if r["is_headliner"]) == 1
        assert sorted(r["billing_order"] for r in rows) == list(range(1, len(rows) + 1))
    # Turnover headlines its own show
    turn = next(r for r in bridge if r["artist_id"] == keys.artist_id("Turnover"))
    assert turn["is_headliner"] is True and turn["billing_order"] == 1


# ---------------------------------------------------------------------------
# FK coverage across the dims (the integrity dim_* must satisfy for the gold join)
# ---------------------------------------------------------------------------

def test_foreign_key_coverage(seed_tm_events):
    geo = d.build_dim_geo(d.read_reference_geo())
    venues = d.build_dim_venue(seed_tm_events, GEO)
    artists = d.build_dim_artist(seed_tm_events, {}, {})
    events = d.build_dim_event(seed_tm_events)
    bridge = d.build_bridge_event_artist(seed_tm_events)

    geo_codes = {g["dma_code"] for g in geo}
    venue_ids = {v["venue_id"] for v in venues}
    artist_ids = {a["artist_id"] for a in artists}

    assert {v["dma_code"] for v in venues} <= geo_codes
    assert {e["venue_id"] for e in events} <= venue_ids
    assert {b["artist_id"] for b in bridge} <= artist_ids
