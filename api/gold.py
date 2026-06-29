"""Gold-layer access for the event demand API.

BigQuery is the only runtime I/O. The repository itself is pure pandas so tests can
inject small in-memory frames and verify the API contract without GCP credentials.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

import pandas as pd

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"

SUMMARY_COLUMNS = [
    "event_id",
    "event_name",
    "artist_name",
    "venue_name",
    "city",
    "state_code",
    "show_date",
    "status_code",
    "price_min",
    "price_max",
    "local_interest",
    "yt_subscribers",
    "yt_views",
    "forecast_price",
]

HISTORY_COLUMNS = [
    "snapshot_date",
    "days_to_show",
    "price_min",
    "price_max",
    "local_interest",
    "yt_subscribers",
    "yt_views",
]

FORECAST_COLUMNS = ["days_to_show", "predicted_price"]


@dataclass(frozen=True)
class GoldFrames:
    fact: pd.DataFrame
    forecast: pd.DataFrame
    dim_event: pd.DataFrame
    dim_venue: pd.DataFrame
    dim_artist: pd.DataFrame


def _to_numpy_backed(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize BigQuery pandas nullable extension dtypes for JSON shaping."""
    for col in df.columns:
        dtype = df[col].dtype
        if not pd.api.types.is_extension_array_dtype(dtype):
            continue
        if pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype):
            df[col] = df[col].astype("float64")
        else:
            df[col] = df[col].astype("object")
    return df


def load_gold_frames(project: str | None = None, dataset: str | None = None) -> GoldFrames:
    """Read the API's gold tables from BigQuery.

    Imports BigQuery lazily so importing the FastAPI app and running offline tests do
    not require the Google SDK or application-default credentials.
    """
    from google.cloud import bigquery

    project = project or os.environ.get("DBT_GCP_PROJECT", DEFAULT_PROJECT)
    dataset = dataset or os.environ.get("DBT_BQ_DATASET", DEFAULT_DATASET)
    client = bigquery.Client(project=project)

    def q(table: str) -> pd.DataFrame:
        df = client.query(f"SELECT * FROM `{project}.{dataset}.{table}`").to_dataframe()
        return _to_numpy_backed(df)

    return GoldFrames(
        fact=q("fact_event_demand"),
        forecast=q("forecast_event_price"),
        dim_event=q("dim_event"),
        dim_venue=q("dim_venue"),
        dim_artist=q("dim_artist"),
    )


def _clean(value: Any) -> Any:
    """Return a JSON-safe scalar: pandas NA/NaN -> None, dates -> ISO strings."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _records(df: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    """Serialize selected dataframe columns into JSON-safe dictionaries."""
    safe = df.reindex(columns=columns)
    return [{col: _clean(value) for col, value in row.items()} for row in safe.to_dict("records")]


class GoldRepository:
    """Query-shaped pandas repository over the gold fact, forecast, and dimensions."""

    def __init__(self, frames: GoldFrames):
        self._frames = frames
        self._latest = self._build_latest()

    def _build_latest(self) -> pd.DataFrame:
        fact = self._frames.fact.copy()
        if fact.empty:
            return fact.reindex(columns=SUMMARY_COLUMNS)

        fact["snapshot_date"] = pd.to_datetime(fact["snapshot_date"], errors="coerce")
        latest_idx = fact.groupby("event_id", dropna=False)["snapshot_date"].idxmax()
        latest = fact.loc[latest_idx].copy()

        latest = latest.merge(
            self._frames.dim_event[["event_id", "event_name", "show_date", "venue_id"]],
            on="event_id",
            how="left",
            suffixes=("", "_event"),
        )
        if "venue_id_event" in latest.columns:
            latest["venue_id"] = latest["venue_id"].combine_first(latest["venue_id_event"])
            latest = latest.drop(columns=["venue_id_event"])

        latest = latest.merge(
            self._frames.dim_venue[["venue_id", "venue_name", "city", "state_code"]],
            on="venue_id",
            how="left",
        )
        latest = latest.merge(
            self._frames.dim_artist[["artist_id", "artist_name"]],
            on="artist_id",
            how="left",
        )
        latest = latest.merge(self._forecast_latest(), on="event_id", how="left")

        latest["show_date"] = pd.to_datetime(latest["show_date"], errors="coerce")
        return latest.sort_values(["show_date", "event_name", "event_id"], na_position="last")

    def _forecast_latest(self) -> pd.DataFrame:
        forecast = self._frames.forecast.copy()
        if forecast.empty:
            return pd.DataFrame(columns=["event_id", "forecast_price"])

        forecast["days_to_show"] = pd.to_numeric(forecast["days_to_show"], errors="coerce")
        forecast["predicted_price"] = pd.to_numeric(forecast["predicted_price"], errors="coerce")
        nearest_idx = forecast.groupby("event_id", dropna=False)["days_to_show"].idxmin()
        return forecast.loc[nearest_idx, ["event_id", "predicted_price"]].rename(
            columns={"predicted_price": "forecast_price"}
        )

    def list_shows(self) -> list[dict[str, Any]]:
        """Return one latest-snapshot summary per event, soonest show first."""
        return _records(self._latest, SUMMARY_COLUMNS)

    def get_show(self, event_id: str) -> dict[str, Any] | None:
        """Return one show summary plus history and forecast series."""
        matches = self._latest[self._latest["event_id"] == event_id]
        if matches.empty:
            return None

        show = _records(matches.head(1), SUMMARY_COLUMNS)[0]
        show["history"] = self._history(event_id)
        show["forecast"] = self._forecast(event_id)
        return show

    def _history(self, event_id: str) -> list[dict[str, Any]]:
        fact = self._frames.fact[self._frames.fact["event_id"] == event_id].copy()
        if fact.empty:
            return []

        fact["snapshot_date"] = pd.to_datetime(fact["snapshot_date"], errors="coerce")
        fact = fact.sort_values("snapshot_date")
        return _records(fact, HISTORY_COLUMNS)

    def _forecast(self, event_id: str) -> list[dict[str, Any]]:
        forecast = self._frames.forecast[self._frames.forecast["event_id"] == event_id].copy()
        if forecast.empty:
            return []

        forecast["days_to_show"] = pd.to_numeric(forecast["days_to_show"], errors="coerce")
        forecast = forecast.sort_values("days_to_show", ascending=False)
        return _records(forecast, FORECAST_COLUMNS)


_repo: GoldRepository | None = None


def set_repository(repository: GoldRepository | None) -> None:
    """Swap the process-wide repository; tests pass None to reset lazy loading."""
    global _repo
    _repo = repository


def get_repository() -> GoldRepository:
    """Return the process-wide repository, lazily loading gold data on first use."""
    global _repo
    if _repo is None:
        _repo = GoldRepository(load_gold_frames())
    return _repo
