"""D2 — forecast each show's price trajectory from today to its show date.

For every upcoming event we take its **latest observed** feature row (closest snapshot
to the show) and sweep `days_to_show` from there down to 0, holding the demand signals
constant — an "all else equal, price vs. time-to-show" curve. This is the line the
demo chart overlays on the price/popularity history.

Assumption (documented for Q&A): signals are held at their latest value rather than
projected forward — honest given the thin history; projecting them would compound model
error. Predictions are floored at 0 (a ticket price can't be negative).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import features  # D1


def latest_features_per_event(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per event — the most recent snapshot (smallest non-negative days_to_show)."""
    idx = frame.groupby("event_id")["days_to_show"].idxmin()
    return frame.loc[idx].reset_index(drop=True)


def build_forecast_frame(latest: pd.DataFrame) -> pd.DataFrame:
    """Expand each event into a row per day from its current days_to_show down to 0."""
    rows = []
    for row in latest.itertuples(index=False):
        record = {col: getattr(row, col) for col in features.FEATURE_COLUMNS}
        current = int(record["days_to_show"])
        for d in range(current, -1, -1):
            rows.append({**record, "days_to_show": d, "event_id": row.event_id})
    out = pd.DataFrame(rows)
    out["genre"] = out["genre"].astype("category")
    return out


def predict_prices(model, forecast_frame: pd.DataFrame) -> pd.DataFrame:
    """Add `predicted_price` (floored at 0) for every forecast row.

    Aligns to the model's fitted columns (`feature_names_in_`) — degenerate features
    dropped at train time aren't passed back in.
    """
    preds = model.predict(forecast_frame[list(model.feature_names_in_)])
    out = forecast_frame.copy()
    out["predicted_price"] = np.clip(preds, 0, None)
    return out
