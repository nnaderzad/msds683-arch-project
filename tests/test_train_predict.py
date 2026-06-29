"""D2 — train/predict unit tests: deterministic reproducibility + forecast shape/sanity.

Offline, fixture-driven. The reproducibility test is the D2 done-when (same input →
same prediction); the forecast test checks the per-show today→show-date trajectory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "model"))

features = pytest.importorskip("features", reason="model deps not installed")
train = pytest.importorskip("train", reason="scikit-learn not installed")
predict = pytest.importorskip("predict", reason="model deps not installed")


def _training_frame():
    gold = pd.DataFrame({
        "event_id": ["e1"] * 4 + ["e2"] * 4,
        "snapshot_date": ["2026-06-18", "2026-06-19", "2026-06-20", "2026-06-21"] * 2,
        "venue_id": [1, 1, 1, 1, 2, 2, 2, 2],
        "days_to_show": [12, 11, 10, 9, 6, 5, 4, 3],
        "price_min": [40., 42., 45., 47., 80., 82., 85., 88.],
        "price_max": [90.] * 4 + [160.] * 4,
        "price_currency": ["USD"] * 8,
        "local_interest": [30, 32, 35, 38, 60, 62, 65, None],
        "yt_subscribers": [1000] * 4 + [5000] * 4,
        "yt_views": [20000] * 4 + [90000] * 4,
    })
    dim_venue = pd.DataFrame({"venue_id": [1, 2], "capacity": [1500, 4000]})
    dim_event = pd.DataFrame({"event_id": ["e1", "e2"], "primary_genre": ["Rock", "Pop"]})
    return features.build_training_frame(gold, dim_venue, dim_event)


def test_training_is_reproducible():
    """Same data + same seed → identical predictions (determinism rule)."""
    frame = _training_frame()
    X, y = features.split_X_y(frame, target=features.DELTA_TARGET)
    m1 = train.train_model(X, y, seed=42)
    m2 = train.train_model(X, y, seed=42)
    np.testing.assert_array_equal(m1.predict(X), m2.predict(X))


def test_forecast_trajectory_shape_and_sanity():
    frame = _training_frame()
    X, y = features.split_X_y(frame, target=features.DELTA_TARGET)
    model = train.train_model(X, y, seed=42)

    latest = predict.latest_features_per_event(frame)
    assert set(latest["event_id"]) == {"e1", "e2"}

    out = predict.predict_prices(model, predict.build_forecast_frame(latest))
    # Each event forecast spans days_to_show from its latest value down to 0.
    for event_id, grp in out.groupby("event_id"):
        current = int(latest.loc[latest["event_id"] == event_id, "days_to_show"].iloc[0])
        assert sorted(grp["days_to_show"]) == list(range(0, current + 1))
    assert (out["predicted_price"] >= 0).all()       # price floor
    assert np.isfinite(out["predicted_price"]).all()
    assert not out.duplicated(["event_id", "days_to_show"]).any()


def test_forecast_starts_at_latest_real_price():
    """Anchor continuity: the forecast at 'today' equals each show's latest real price."""
    frame = _training_frame()
    X, y = features.split_X_y(frame, target=features.DELTA_TARGET)
    model = train.train_model(X, y, seed=42)

    latest = predict.latest_features_per_event(frame)
    out = predict.predict_prices(model, predict.build_forecast_frame(latest))
    for event_id, grp in out.groupby("event_id"):
        row = latest.loc[latest["event_id"] == event_id]
        current = int(row["days_to_show"].iloc[0])
        latest_price = float(row[features.DEFAULT_TARGET].iloc[0])
        today_pred = grp.loc[grp["days_to_show"] == current, "predicted_price"].iloc[0]
        assert today_pred == pytest.approx(latest_price)
