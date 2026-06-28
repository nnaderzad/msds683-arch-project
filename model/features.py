"""D1 — model feature build: assemble the pooled training frame from gold.

The forecast is cross-sectional/panel, not per-show time series: with only ~2 weeks of
snapshot history we **pool across shows**. Each row is one priced (event, snapshot)
observation; D2 learns price as a function of `days_to_show` + exogenous demand signals
across all shows, then forecasts each show's price toward its date.

Design (defend in Q&A):
  * **No target leakage.** Features deliberately EXCLUDE `price_*` (the target side) and
    anything derived from it. `split_X_y` only ever returns `FEATURE_COLUMNS`.
  * **Signals are nullable** (LEFT-joined in gold). We keep NaN — D2's
    `HistGradientBoostingRegressor` handles missing values natively — and add
    **missingness flags** so "no signal collected" is itself a feature (honest about
    our partial Trends/YouTube coverage).
  * **Priced, pre-show rows only.** Training needs a target, so unpriced rows are
    dropped; post-show snapshots (`days_to_show < 0`) are dropped as out-of-scope.

`local_interest` is the Google Trends **local (DMA) popularity** for the headliner in
the event's metro; `yt_*` are YouTube popularity. Pure functions take dataframes
(testable offline on a fixture); `read_gold_and_dims` is the BigQuery I/O layer.
"""

from __future__ import annotations

import os

import pandas as pd

DEFAULT_TARGET = "price_min"
# Target side — these must NEVER appear in the feature matrix (leakage guard).
PRICE_COLUMNS = ["price_min", "price_max", "price_currency"]

NUMERIC_FEATURES = ["days_to_show", "local_interest", "yt_subscribers", "yt_views", "capacity"]
CATEGORICAL_FEATURES = ["genre"]
MISSINGNESS_FLAGS = ["has_interest", "has_youtube"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES + MISSINGNESS_FLAGS
ID_COLUMNS = ["event_id", "snapshot_date"]


def build_training_frame(
    gold: pd.DataFrame,
    dim_venue: pd.DataFrame,
    dim_event: pd.DataFrame,
    *,
    target: str = DEFAULT_TARGET,
) -> pd.DataFrame:
    """Enrich gold with capacity/genre, add missingness flags, keep priced pre-show rows.

    Returns a frame of ID_COLUMNS + FEATURE_COLUMNS + [target] — model-ready, no leakage.
    """
    df = gold.merge(dim_venue[["venue_id", "capacity"]], on="venue_id", how="left")
    df = df.merge(
        dim_event[["event_id", "primary_genre"]].rename(columns={"primary_genre": "genre"}),
        on="event_id", how="left",
    )

    # Missingness is informative given partial Trends/YouTube coverage.
    df["has_interest"] = df["local_interest"].notna()
    df["has_youtube"] = df["yt_views"].notna()
    df["genre"] = df["genre"].astype("category")

    # Train only on priced, pre-show observations.
    keep = df[target].notna() & (df["days_to_show"] >= 0)
    return df.loc[keep, ID_COLUMNS + FEATURE_COLUMNS + [target]].reset_index(drop=True)


def split_X_y(frame: pd.DataFrame, *, target: str = DEFAULT_TARGET):
    """Return (X, y). X is exactly FEATURE_COLUMNS — never the target or price fields."""
    return frame[FEATURE_COLUMNS].copy(), frame[target].copy()


def categorical_mask() -> list[bool]:
    """Boolean mask over FEATURE_COLUMNS marking categoricals (for D2's regressor)."""
    return [col in CATEGORICAL_FEATURES for col in FEATURE_COLUMNS]


# ---------------------------------------------------------------------------
# BigQuery I/O (the real run; unit tests use an in-memory fixture instead)
# ---------------------------------------------------------------------------

def read_gold_and_dims(
    project: str | None = None, dataset: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read gold `fact_event_demand` + `dim_venue` + `dim_event` from BigQuery (ADC)."""
    from google.cloud import bigquery

    project = project or os.environ.get("DBT_GCP_PROJECT", "data-architecture-498123")
    dataset = dataset or os.environ.get("DBT_BQ_DATASET", "event_demand_analytics")
    client = bigquery.Client(project=project)

    def q(table: str) -> pd.DataFrame:
        return client.query(f"SELECT * FROM `{project}.{dataset}.{table}`").to_dataframe()

    return q("fact_event_demand"), q("dim_venue"), q("dim_event")
