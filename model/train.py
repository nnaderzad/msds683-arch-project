"""D2 — train the pooled price regressor (deterministic, fixed seed).

Cross-sectional/panel model (thin ~2-week history → pool across shows, NOT per-show
ARIMA): learns ticket price as a function of `days_to_show` + exogenous demand signals
(Trends local popularity, YouTube, capacity, genre) over every priced (event, snapshot)
row. `HistGradientBoostingRegressor` is chosen because it handles **missing values**
(our signals are partially collected) and **categoricals** (`genre`) natively, and is
**deterministic** with a fixed `random_state` — re-runnable per our determinism rule.

*Rejected:* per-show ARIMA/Prophet (not enough history); linear models (need imputation
+ one-hot, brittle on sparse signals).
"""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

SEED = 42


def trainable_columns(X: pd.DataFrame) -> list[str]:
    """Columns with ≥2 distinct non-null values — the only ones the regressor can bin.

    Drops degenerate features (e.g. an all-null `capacity` — Ticketmaster rarely exposes
    it — or a constant flag), which would otherwise crash HGB's histogram binning.
    """
    return [c for c in X.columns if X[c].nunique(dropna=True) >= 2]


def train_model(X: pd.DataFrame, y: pd.Series, *, seed: int = SEED) -> HistGradientBoostingRegressor:
    """Fit the deterministic pooled regressor on the usable feature subset.

    `genre` is treated as categorical (from its category dtype). The fitted model records
    its `feature_names_in_`, which `predict.predict_prices` aligns to.
    """
    cols = trainable_columns(X)
    model = HistGradientBoostingRegressor(
        random_state=seed,
        categorical_features="from_dtype",  # auto-detect pandas category cols (genre)
    )
    model.fit(X[cols], y)
    return model
