"""D2 — forecast each show's price trajectory from today to its show date.

For every upcoming event we take its **latest observed** feature row (closest snapshot
to the show) and sweep `days_to_show` from there down to 0, holding the demand signals
constant — an "all else equal, price vs. time-to-show" curve. This is the line the
demo chart overlays on the price/popularity history.

**Anchor + drift.** The model predicts `price_delta` (deviation from a show's price), not
the level. We reconstruct the level by **anchoring on the latest observed real price** and
adding the model's *relative* drift: predicted(d) = latest_real_price + drift(d) − drift(now).
Subtracting the drift at "today" re-centers the curve so it passes **exactly through the
show's last real price** (visual continuity with the history chart) and then bends by the
pooled price-vs-time-to-show profile. Because the level comes from a real observed price —
not the tree's output — the forecast cannot range-compress and reaches any real price level.

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
    """Expand each event into a row per day from its current days_to_show down to 0.

    Carries the show's latest real price (`latest_real_price`) and current day
    (`current_days`) so `predict_prices` can anchor the level and re-center the drift.
    """
    rows = []
    for row in latest.itertuples(index=False):
        record = {col: getattr(row, col) for col in features.FEATURE_COLUMNS}
        current = int(record["days_to_show"])
        latest_real_price = getattr(row, features.DEFAULT_TARGET)  # price_min at latest snapshot
        for d in range(current, -1, -1):
            rows.append({**record, "days_to_show": d, "event_id": row.event_id,
                         "latest_real_price": latest_real_price, "current_days": current})
    out = pd.DataFrame(rows)
    out["genre"] = out["genre"].astype("category")
    return out


def predict_prices(model, forecast_frame: pd.DataFrame) -> pd.DataFrame:
    """Add `predicted_price` (floored at 0): anchor on the real price + re-centered drift.

    The model emits `price_delta` (deviation). We rebuild the level as
    `latest_real_price + raw_drift(d) − raw_drift(today)`, so the curve starts at the show's
    last real price and only the model's *relative* drift moves it. Aligns to the model's
    fitted columns (`feature_names_in_`) — degenerate features dropped at train time aren't
    passed back in.
    """
    out = forecast_frame.copy()
    out["raw_drift"] = model.predict(out[list(model.feature_names_in_)])

    # Re-center each event's drift on "today" (its current days_to_show) so the forecast
    # passes exactly through the latest real price there.
    at_today = out["days_to_show"] == out["current_days"]
    drift_today = out.loc[at_today].set_index("event_id")["raw_drift"]
    out["drift_today"] = out["event_id"].map(drift_today)

    out["predicted_price"] = np.clip(
        out["latest_real_price"] + out["raw_drift"] - out["drift_today"], 0, None)
    return out
