"""Offline tests for the demand-priority roster ranking in build_roster.py.

No network/BQ: feeds inline (artist, event) rows shaped like the fetch_upcoming output
(BigQuery Rows behave like dicts here). GeoLookup uses the committed reference CSVs.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "google_trends_api"))

import build_roster as br  # noqa: E402
from geo_lookup import GeoLookup  # noqa: E402

GEO = GeoLookup()
BAY = "94115"   # San Francisco -> DMA 807
NY = "10001"    # New York -> a non-Bay DMA


def row(artist, *, zip_code=NY, state="NY", local_date="2026-08-01",
        genre="Rock", price_min=None):
    """One (artist, event) row, like fetch_upcoming returns (price_min None = unpriced)."""
    return {"artist": artist, "zip": zip_code, "state": state,
            "local_date": local_date, "genre": genre, "price_min": price_min}


def _by_artist(frame):
    return {r["artist"]: r for r in frame.to_dict("records")}


def test_priced_headliner_outranks_bigger_touring_act():
    # TouringAct: many markets, NO price. PricedAct: one market, but PRICED.
    rows = (
        [row("TouringAct", zip_code=NY, state="NY", local_date="2026-07-01")]
        + [row("TouringAct", zip_code="60601", state="IL", local_date="2026-07-02")]
        + [row("TouringAct", zip_code="90012", state="CA", local_date="2026-07-03")]
        + [row("PricedAct", zip_code=NY, state="NY", price_min=55.0)]
    )
    artist, _, stats = br.build_frames(rows, GEO, top_n=1)
    a = _by_artist(artist)
    # Priced act ranks #1 despite far less touring breadth.
    assert a["PricedAct"]["rank"] == 1
    assert a["PricedAct"]["n_priced_events"] == 1
    assert a["TouringAct"]["n_priced_events"] == 0
    assert a["PricedAct"]["selected"] and not a["TouringAct"]["selected"]
    assert stats["selected_with_priced_event"] == 1


def test_bay_area_and_edm_boost_breaks_ties_among_priced():
    # Three priced acts, equal touring footprint; Bay+EDM should sort to the top.
    rows = [
        row("PlainPriced", zip_code=NY, state="NY", genre="Rock", price_min=40.0),
        row("BayEdm", zip_code=BAY, state="CA", genre="Dance/Electronic", price_min=40.0),
        row("EdmOnly", zip_code=NY, state="NY", genre="Electronic", price_min=40.0),
    ]
    artist, _, _ = br.build_frames(rows, GEO, top_n=3)
    a = _by_artist(artist)
    assert a["BayEdm"]["ba_edm_boost"] == 2      # Bay Area + EDM
    assert a["EdmOnly"]["ba_edm_boost"] == 1     # EDM only
    assert a["PlainPriced"]["ba_edm_boost"] == 0
    assert a["BayEdm"]["rank"] < a["EdmOnly"]["rank"] < a["PlainPriced"]["rank"]


def test_seed_artist_always_selected_even_if_unpriced():
    seed_name = br.config.ARTISTS[0].name
    rows = [
        row(seed_name, zip_code=NY, state="NY", price_min=None),          # unpriced seed
        row("PricedAct", zip_code=NY, state="NY", price_min=99.0),
    ]
    artist, _, _ = br.build_frames(rows, GEO, top_n=1)
    a = _by_artist(artist)
    assert a[seed_name]["selected"]          # in_seed forces selection
    assert a[seed_name]["in_seed"]
