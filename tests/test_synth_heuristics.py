"""Tests for the synthetic-demand heuristics — including the three real-world
anecdotes the model encodes (MGMT/Public Works, Chris Lake/big club, festival
day tickets)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synth import heuristics as h


def rng(seed: int = 683) -> np.random.Generator:
    return np.random.default_rng(seed)


# ---------------------------------------------------------------------------
# Anecdote cases (the model must reproduce the observed market behavior)
# ---------------------------------------------------------------------------


def test_big_artist_small_room_sells_out_and_resells_above_face():
    """MGMT at Public Works (~750 cap): instant sellout, resale >> face."""
    outcome = h.simulate_event(
        event_name="MGMT (DJ set)",
        capacity=750,
        local_interest=85,
        yt_subscribers=1_500_000,
        onsale_horizon_days=45,
        rng=rng(),
    )
    assert outcome.demand_ratio > 2.0
    assert outcome.sellout_probability > 0.95
    assert outcome.sold_out is True
    assert outcome.sellout_days_before_show is not None
    assert outcome.sellout_days_before_show > 20  # sold out long before the show
    assert outcome.resale_multiplier > 1.5


def test_mid_artist_big_room_undersells_and_resells_below_face():
    """Chris Lake-tier act in a big room: no sellout, day-of resale below face."""
    outcome = h.simulate_event(
        event_name="House Night",
        capacity=4500,
        local_interest=35,
        yt_subscribers=400_000,
        onsale_horizon_days=45,
        rng=rng(),
    )
    assert outcome.demand_ratio < 0.7
    assert outcome.sellout_probability < 0.2
    assert outcome.resale_multiplier < 0.95


def test_festival_day_price_anchors_to_headliner_lineup():
    """Outside Lands: 3 headliners with ~$300/$150/$120 solo prices → ~$250 face."""
    face = h.festival_day_face_price([300.0, 150.0, 120.0])
    assert 230 <= face <= 280
    # And the resale side: a festival-scale demand ratio prices ~2x+.
    assert h.resale_multiplier(4.0, rng()) >= 2.0


# ---------------------------------------------------------------------------
# Structural properties
# ---------------------------------------------------------------------------


def test_popularity_monotone_in_both_signals():
    base = h.popularity_score(40, 100_000)
    assert h.popularity_score(80, 100_000) > base
    assert h.popularity_score(40, 10_000_000) > base
    assert h.popularity_score(None, None) == 0.0
    assert 0.0 <= h.popularity_score(100, 10**9) <= 1.0


def test_demand_ratio_monotone_in_capacity():
    draw = h.expected_draw(0.6)
    assert h.demand_ratio(draw, 300) > h.demand_ratio(draw, 3000)


def test_sellout_probability_is_logistic_around_one():
    assert h.sellout_probability(0.2) < 0.1
    assert abs(h.sellout_probability(1.0) - 0.5) < 1e-9
    assert h.sellout_probability(3.0) > 0.99


def test_sellout_days_bounds_and_urgency():
    fast = h.sellout_days_before_show(5.0, 45, rng())
    slow = h.sellout_days_before_show(1.2, 45, rng())
    assert fast is not None and slow is not None
    assert 0 <= slow <= fast <= 45
    assert h.sellout_days_before_show(0.8, 45, rng()) is None


def test_resale_multiplier_monotone_bands():
    generator = rng()
    soft = h.resale_multiplier(0.3, generator)
    par = h.resale_multiplier(1.0, generator)
    hot = h.resale_multiplier(2.5, generator)
    assert soft < 0.75
    assert 0.85 <= par <= 1.15
    assert hot > 1.6


def test_event_type_inference():
    assert h.infer_event_type("Outside Lands Festival (Saturday)", 20000) == "festival"
    assert h.infer_event_type("Club Night", 400) == "club"
    assert h.infer_event_type("Arena Tour", 15000) == "concert"
    assert h.effective_capacity(None, "club") == h.TIER_DEFAULT_CAPACITY["club"]
    assert h.effective_capacity(750, "club") == 750


def test_same_seed_reproduces_identical_outcomes():
    kwargs = dict(
        event_name="Warehouse Rave",
        capacity=600,
        local_interest=70,
        yt_subscribers=250_000,
        onsale_horizon_days=30,
    )
    first = h.simulate_event(rng=rng(42), **kwargs)
    second = h.simulate_event(rng=rng(42), **kwargs)
    assert first == second
    third = h.simulate_event(rng=rng(43), **kwargs)
    assert isinstance(third, h.DemandOutcome)  # different seed still valid, may differ


@pytest.mark.parametrize("ratio", [0.1, 0.5, 1.0, 1.5, 3.0, 10.0])
def test_resale_multiplier_reasonable_range(ratio):
    value = h.resale_multiplier(ratio, rng())
    assert 0.4 <= value <= 3.7
