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
    """Train on priced history, forecast every event today→show date. Returns the table.

    The model targets `price_delta` (deviation from each show's anchor price); predict.py
    rebuilds the level by anchoring on the latest real price.
    """
    frame = features.build_training_frame(gold, dim_venue, dim_event)
    X, y = features.split_X_y(frame, target=features.DELTA_TARGET)
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


# ---------------------------------------------------------------------------
# Entrypoint — the real run: read gold from BQ → forecast → write gold table.
# Deterministic + idempotent (fixed seed; WRITE_TRUNCATE). Re-runnable, no LLM.
#   python pipeline/gold/export_predictions_table.py --dry-run   # report only
#   python pipeline/gold/export_predictions_table.py             # materialize
# ---------------------------------------------------------------------------

def run(*, dry_run: bool = False, project: str | None = None, dataset: str | None = None) -> int:
    """Read gold + dims from BigQuery, assemble the forecast, and (unless dry_run) write
    the `forecast_event_price` gold table. Returns the forecast row count."""
    gold, dim_venue, dim_event = features.read_gold_and_dims(project, dataset)
    forecast = assemble_forecast(gold, dim_venue, dim_event)
    n_rows, n_events = len(forecast), forecast["event_id"].nunique()
    if dry_run:
        print(f"[forecast] DRY-RUN: assembled {n_rows} rows / {n_events} events; nothing written.")
        return n_rows
    written = to_bigquery(forecast, project, dataset)
    print(f"[forecast] wrote {written} rows / {n_events} events to {FORECAST_TABLE}.")
    return written


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Materialize the gold forecast_event_price table.")
    parser.add_argument("--dry-run", action="store_true", help="Assemble + report counts; write nothing.")
    parser.add_argument("--project", default=None, help="GCP project (default: $DBT_GCP_PROJECT).")
    parser.add_argument("--dataset", default=None, help="BQ dataset (default: $DBT_BQ_DATASET).")
    args = parser.parse_args(argv)
    run(dry_run=args.dry_run, project=args.project, dataset=args.dataset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
