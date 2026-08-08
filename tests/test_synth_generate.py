"""Offline tests for the synth generator's pure per-event transform."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synth import generate as g
from synth import heuristics as h


def feature(**overrides) -> dict:
    base = {
        "event_id": "evt1",
        "event_name": "Warehouse Rave",
        "show_date": "2026-09-01",
        "primary_genre": "Dance/Electronic",
        "venue_name": "Public Works",
        "dma_code": "807",
        "capacity": 750,
        "artist_name": "DJ Fixture",
        "yt_subscribers": 1_500_000,
        "local_interest_level": 85.0,
        "observed_price": 45.0,
        "genre_median_price": 38.0,
    }
    base.update(overrides)
    return base


def test_simulate_row_shapes_and_provenance():
    demand, series = g.simulate_row(feature(), base_seed=683, run_id="r1")
    assert demand["capacity_source"] == "researched"
    assert demand["face_price"] == 45.0 and demand["face_price_source"] == "observed"
    assert demand["synth_run_id"] == "r1"
    assert demand["generator_version"] == g.GENERATOR_VERSION
    assert demand["resale_price_at_show"] == pytest.approx(
        demand["face_price"] * demand["resale_multiplier"]
    )
    assert [s["days_to_show"] for s in series] == g.SERIES_CHECKPOINTS
    assert all(s["synth_run_id"] == "r1" for s in series)


def test_series_converges_to_final_multiplier():
    demand, series = g.simulate_row(feature(), base_seed=683, run_id="r1")
    at_show = next(s for s in series if s["days_to_show"] == 0)
    at_onsale = next(s for s in series if s["days_to_show"] == g.ONSALE_HORIZON_DAYS)
    assert at_show["resale_multiplier"] == pytest.approx(demand["resale_multiplier"], abs=0.01)
    assert at_onsale["resale_multiplier"] == pytest.approx(1.0, abs=0.01)


def test_determinism_same_seed_same_world_any_order():
    first, series_a = g.simulate_row(feature(), base_seed=683, run_id="r1")
    second, series_b = g.simulate_row(feature(), base_seed=683, run_id="r1")
    assert first == second and series_a == series_b
    different_seed, _ = g.simulate_row(feature(), base_seed=1, run_id="r1")
    assert different_seed["seed"] != first["seed"]


def test_per_event_rng_streams_are_independent():
    a = g.event_rng(683, "evt1").random()
    b = g.event_rng(683, "evt2").random()
    assert a != b
    assert g.event_rng(683, "evt1").random() == a


def test_face_price_fallback_chain():
    demand, _ = g.simulate_row(feature(observed_price=None), base_seed=683, run_id="r")
    assert demand["face_price"] == 38.0 and demand["face_price_source"] == "genre_median"
    demand, _ = g.simulate_row(
        feature(observed_price=None, genre_median_price=None), base_seed=683, run_id="r"
    )
    assert demand["face_price_source"] == "type_default"


def test_tier_estimate_labeled_when_capacity_unknown():
    demand, _ = g.simulate_row(feature(capacity=None), base_seed=683, run_id="r")
    assert demand["capacity_source"] == "tier_estimate"
    assert demand["capacity"] == h.TIER_DEFAULT_CAPACITY[demand["event_type"]]


def test_sellout_date_derived_from_show_date():
    demand, _ = g.simulate_row(feature(), base_seed=683, run_id="r")
    if demand["sold_out"]:
        assert demand["sellout_date"] is not None
        assert demand["sellout_date"] <= demand["show_date"]
    else:
        assert demand["sellout_date"] is None


def test_resale_multiplier_at_bounds():
    assert h.resale_multiplier_at(2.0, 45, 45) == 1.0
    assert h.resale_multiplier_at(2.0, 0, 45) == 2.0
    mid = h.resale_multiplier_at(2.0, 20, 45)
    assert 1.0 < mid < 2.0
