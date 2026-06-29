"""D1 — unit tests for the model feature build (offline, fixture-driven).

Checks feature shapes, the priced/pre-show filtering, missingness flags, and — the
key invariant — **no target leakage** (price never reaches the feature matrix).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "model"))

features = pytest.importorskip("features", reason="pandas not installed")


def _fixture():
    """A tiny gold + dims slice exercising every code path."""
    gold = pd.DataFrame({
        "event_id":     ["e1", "e1", "e2", "e3", "e4"],
        "snapshot_date": ["2026-06-20", "2026-06-21", "2026-06-20", "2026-06-20", "2026-06-20"],
        "venue_id":     [1, 1, 2, 3, 1],
        "days_to_show": [10, 9, 5, 3, -1],          # e4 is post-show → dropped
        "price_min":    [50.0, 52.0, None, 30.0, 40.0],  # e2 unpriced → dropped
        "price_max":    [100.0, 104.0, None, 60.0, 80.0],
        "price_currency": ["USD"] * 5,
        "local_interest": [40, 42, None, None, 10],  # Google Trends local popularity
        "yt_subscribers": [1000, 1000, None, 500, 500],
        "yt_views":     [20000, 20000, None, 8000, 8000],
    })
    dim_venue = pd.DataFrame({"venue_id": [1, 2, 3], "capacity": [2000, None, 500]})
    dim_event = pd.DataFrame({"event_id": ["e1", "e2", "e3", "e4"],
                              "primary_genre": ["Rock", "Pop", "Rock", "Jazz"]})
    return gold, dim_venue, dim_event


def test_keeps_only_priced_preshow_rows():
    frame = features.build_training_frame(*_fixture())
    # e1×2 (days 10, 9) + e3 (day 3) survive; e2 unpriced and e4 post-show are dropped.
    assert len(frame) == 3
    assert set(frame["event_id"]) == {"e1", "e3"}
    assert (frame["days_to_show"] >= 0).all()
    assert frame["price_min"].notna().all()


def test_no_target_leakage():
    frame = features.build_training_frame(*_fixture())
    X, y = features.split_X_y(frame)
    for price_col in features.PRICE_COLUMNS:
        assert price_col not in X.columns, f"leakage: {price_col} reached the features"
    assert "price_min" not in X.columns
    assert y.name == "price_min"
    assert len(X) == len(y) == len(frame)


def test_feature_shape_and_columns():
    frame = features.build_training_frame(*_fixture())
    X, _ = features.split_X_y(frame)
    assert list(X.columns) == features.FEATURE_COLUMNS
    assert X.shape[1] == len(features.FEATURE_COLUMNS)
    assert len(features.categorical_mask()) == len(features.FEATURE_COLUMNS)
    assert sum(features.categorical_mask()) == len(features.CATEGORICAL_FEATURES)


def test_missingness_flags_and_enrichment():
    frame = features.build_training_frame(*_fixture())
    by_key = frame.set_index(["event_id", "snapshot_date"])
    # e3 has no Trends signal → has_interest False; e1 has it → True.
    assert not by_key.loc[("e3", "2026-06-20"), "has_interest"]
    assert by_key.loc[("e1", "2026-06-20"), "has_interest"]
    # capacity joined from dim_venue; genre joined from dim_event.
    assert by_key.loc[("e1", "2026-06-20"), "capacity"] == 2000
    assert by_key.loc[("e1", "2026-06-20"), "genre"] == "Rock"


def test_anchor_is_earliest_price_and_delta_target():
    """Anchor = each show's earliest observed price; target = deviation from it."""
    frame = features.build_training_frame(*_fixture())
    by_key = frame.set_index(["event_id", "snapshot_date"])
    # e1's earliest priced snapshot is 06-20 @ $50 → anchor 50; delta 0 then +2 on 06-21 @ $52.
    assert by_key.loc[("e1", "2026-06-20"), features.ANCHOR_COLUMN] == 50.0
    assert by_key.loc[("e1", "2026-06-20"), features.DELTA_TARGET] == 0.0
    assert by_key.loc[("e1", "2026-06-21"), features.DELTA_TARGET] == 2.0
    # e3 has a single priced row @ $30 → anchor 30, delta 0 (the "fall back to lone price" case).
    assert by_key.loc[("e3", "2026-06-20"), features.ANCHOR_COLUMN] == 30.0
    assert by_key.loc[("e3", "2026-06-20"), features.DELTA_TARGET] == 0.0

    # Training on the delta target still leaks no price into X.
    X, y = features.split_X_y(frame, target=features.DELTA_TARGET)
    assert y.name == features.DELTA_TARGET
    assert features.ANCHOR_COLUMN not in X.columns
    assert features.DELTA_TARGET not in X.columns
    assert list(X.columns) == features.FEATURE_COLUMNS
