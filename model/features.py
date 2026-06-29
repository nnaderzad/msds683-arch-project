"""D1 — model feature build: assemble the pooled training frame from gold.

The forecast is cross-sectional/panel, not per-show time series: with only ~2 weeks of
snapshot history we **pool across shows**. Each row is one priced (event, snapshot)
observation. We measured (eda/diagnose_price_movement.py) that ticket price is ~96% **flat**
across each show's window — so this is a price-LEVEL problem, not a drift problem. The model
therefore anchors each show at its own observed price and learns only the small DEVIATION
(`price_delta`) from it as a function of `days_to_show` + exogenous demand signals; predict.py
adds a real price back, so the forecast starts at each show's real level and spans the full
price range.

Design (defend in Q&A):
  * **No target leakage.** Features deliberately EXCLUDE `price_*` and the price-derived
    `anchor_price`/`price_delta` (the target side). `split_X_y` only ever returns
    `FEATURE_COLUMNS`. The anchor is each show's **earliest** observed price (a known past
    observation), used only as a target offset — not a feature — so no future info enters X.
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
ANCHOR_COLUMN = "anchor_price"   # per-show price anchor = earliest observed price (known past)
DELTA_TARGET = "price_delta"     # model target = price_min - anchor_price (deviation from anchor)
# Target side — these must NEVER appear in the feature matrix (leakage guard). The anchor
# and delta are price-derived, so they are listed here too: they enter only as the target
# transform / a level offset added back at predict time, never as a feature.
PRICE_COLUMNS = ["price_min", "price_max", "price_currency", ANCHOR_COLUMN, DELTA_TARGET]

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

    # Force numeric dtype: an all-null column (e.g. capacity — Ticketmaster rarely
    # exposes it) otherwise stays `object`, which the regressor can't ingest. NaN is fine.
    for col in NUMERIC_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Train only on priced, pre-show observations.
    keep = df[target].notna() & (df["days_to_show"] >= 0)
    kept = df.loc[keep].sort_values(ID_COLUMNS).copy()

    # Per-show price ANCHOR = the show's earliest observed price — a known PAST observation,
    # so no future leakage. The model targets the DEVIATION from it (`price_delta`); at
    # forecast time predict.py adds a real price back analytically, so predictions span the
    # full price range (no tree-extrapolation cap → no range-compression) and start at each
    # show's real level. Ticket prices are ~96% flat across our window (see
    # eda/diagnose_price_movement.py): the anchor carries the level, and the pooled model
    # only has to learn the small drift of the ~4% of shows that actually move.
    kept[ANCHOR_COLUMN] = kept.groupby("event_id")[target].transform("first")
    kept[DELTA_TARGET] = kept[target] - kept[ANCHOR_COLUMN]
    cols = ID_COLUMNS + FEATURE_COLUMNS + [target, ANCHOR_COLUMN, DELTA_TARGET]
    return kept[cols].reset_index(drop=True)


def split_X_y(frame: pd.DataFrame, *, target: str = DEFAULT_TARGET):
    """Return (X, y). X is exactly FEATURE_COLUMNS — never the target or price fields."""
    return frame[FEATURE_COLUMNS].copy(), frame[target].copy()


def categorical_mask() -> list[bool]:
    """Boolean mask over FEATURE_COLUMNS marking categoricals (for D2's regressor)."""
    return [col in CATEGORICAL_FEATURES for col in FEATURE_COLUMNS]


# ---------------------------------------------------------------------------
# BigQuery I/O (the real run; unit tests use an in-memory fixture instead)
# ---------------------------------------------------------------------------

def _to_numpy_backed(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize BigQuery's pandas *nullable* extension dtypes to numpy-backed columns.

    `to_dataframe()` returns INTEGER/FLOAT/BOOL as `Int64`/`Float64`/`boolean` carrying
    `pd.NA`; sklearn/numpy require `np.nan` in a float array. Numeric/bool extension
    columns → `float64` (NA→NaN); other extension columns (nullable string, `dbdate`)
    → `object`. No-op on already-numpy columns. (The model was unit-tested on plain-numpy
    seed fixtures, so this quirk only surfaces on the live read.)
    """
    for col in df.columns:
        dtype = df[col].dtype
        if not pd.api.types.is_extension_array_dtype(dtype):
            continue
        if pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype):
            df[col] = df[col].astype("float64")
        else:
            df[col] = df[col].astype("object")
    return df


def read_gold_and_dims(
    project: str | None = None, dataset: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read gold `fact_event_demand` + `dim_venue` + `dim_event` from BigQuery (ADC)."""
    from google.cloud import bigquery

    project = project or os.environ.get("DBT_GCP_PROJECT", "data-architecture-498123")
    dataset = dataset or os.environ.get("DBT_BQ_DATASET", "event_demand_analytics")
    client = bigquery.Client(project=project)

    def q(table: str) -> pd.DataFrame:
        df = client.query(f"SELECT * FROM `{project}.{dataset}.{table}`").to_dataframe()
        return _to_numpy_backed(df)

    return q("fact_event_demand"), q("dim_venue"), q("dim_event")
