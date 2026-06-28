"""D2 — assemble per-show price forecasts and export the gold `forecast_event_price` table.

End-to-end: gold + dims → D1 training frame → D2 model → per-show daily forecast →
gold table. Forecasts are **precomputed into gold** (locked decision) so the API serves
them directly — deterministic, no model-at-request-time.

Pure assembly (`assemble_forecast`) is offline/testable; `to_bigquery` is the I/O layer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "model"))

import features  # noqa: E402  (D1)
import predict as predict_mod  # noqa: E402
import train as train_mod  # noqa: E402

FORECAST_TABLE = "forecast_event_price"
FORECAST_COLUMNS = ["event_id", "days_to_show", "predicted_price"]


def assemble_forecast(
    gold: pd.DataFrame, dim_venue: pd.DataFrame, dim_event: pd.DataFrame, *, seed: int = train_mod.SEED
) -> pd.DataFrame:
    """Train on priced history, forecast every event today→show date. Returns the table."""
    frame = features.build_training_frame(gold, dim_venue, dim_event)
    X, y = features.split_X_y(frame)
    model = train_mod.train_model(X, y, seed=seed)

    latest = predict_mod.latest_features_per_event(frame)
    forecast = predict_mod.predict_prices(model, predict_mod.build_forecast_frame(latest))
    return forecast[FORECAST_COLUMNS].reset_index(drop=True)


def to_bigquery(df: pd.DataFrame, project: str | None = None, dataset: str | None = None) -> int:
    """CREATE OR REPLACE the gold forecast table from the dataframe. Returns row count."""
    from google.cloud import bigquery

    project = project or os.environ.get("DBT_GCP_PROJECT", "data-architecture-498123")
    dataset = dataset or os.environ.get("DBT_BQ_DATASET", "event_demand_analytics")
    client = bigquery.Client(project=project)
    table_id = f"{project}.{dataset}.{FORECAST_TABLE}"
    job = client.load_table_from_dataframe(
        df, table_id,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
    )
    job.result()
    return len(df)
