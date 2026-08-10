"""Gold-layer access for the event demand API.

BigQuery is the only runtime I/O. The repository itself is pure pandas so tests can
inject small in-memory frames and verify the API contract without GCP credentials.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
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
    "primary_genre",
    "dma_code",
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

# The nationwide TM sweep ingests EVERY segment, so the warehouse honestly holds
# sports/theatre/expo events — but the dashboard is a music product, so its genre
# dropdown excludes the clearly-non-music segments. Ambiguous ones (Other, Holiday,
# Fairs & Festivals) stay in: they routinely contain music events. The warehouse
# and the agent are unaffected — non-music events remain queryable.
NON_MUSIC_GENRES = frozenset(
    {
        "Aquatics",
        "Athletic Races",
        "Baseball",
        "Basketball",
        "Boxing",
        "Comedy",
        "Community/Civic",
        "Cultural",
        "Equestrian",
        "Extreme",
        "Family",
        "Fine Art",
        "Food & Drink",
        "Football",
        "Golf",
        "Gymnastics",
        "Hobby/Special Interest Expos",
        "Hockey",
        "Ice Skating",
        "Lacrosse",
        "Magic & Illusion",
        "Martial Arts",
        "Miscellaneous",
        "Miscellaneous Theatre",
        "Motorsports/Racing",
        "Multimedia",
        "Performance Art",
        "Puppetry",
        "Rodeo",
        "Rugby",
        "Soccer",
        "Spectacular",
        "Tennis",
        "Theatre",
        "Tourist Attraction",
        "Undefined",
        "Variety",
        "Wrestling",
    }
)

# The gap-filled price series (fact_event_demand_continuous): real observed prices
# carried forward across interior gaps, every carried row flagged price_is_filled.
HISTORY_FILLED_COLUMNS = [
    "snapshot_date",
    "days_to_show",
    "price_min",
    "price_max",
    "price_is_filled",
]


@dataclass(frozen=True)
class GoldFrames:
    fact: pd.DataFrame
    forecast: pd.DataFrame
    dim_event: pd.DataFrame
    dim_venue: pd.DataFrame
    dim_artist: pd.DataFrame
    # Slim projection of fact_event_demand_continuous (price columns only); defaults
    # empty so existing 5-frame constructions and tests keep working.
    continuous: pd.DataFrame = field(default_factory=pd.DataFrame)


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

    def q(table: str, columns: str = "*") -> pd.DataFrame:
        df = client.query(f"SELECT {columns} FROM `{project}.{dataset}.{table}`").to_dataframe()
        return _to_numpy_backed(df)

    def q_continuous() -> pd.DataFrame:
        # Slim projection keeps the in-memory footprint small; a missing/broken
        # continuous table must never take the honest dashboard down with it.
        try:
            return q(
                "fact_event_demand_continuous",
                "event_id, snapshot_date, days_to_show, price_min, price_max, price_is_filled",
            )
        except Exception:  # noqa: BLE001 - filled view is optional; observed view still serves
            return pd.DataFrame()

    return GoldFrames(
        fact=q("fact_event_demand"),
        forecast=q("forecast_event_price"),
        dim_event=q("dim_event"),
        dim_venue=q("dim_venue"),
        dim_artist=q("dim_artist"),
        continuous=q_continuous(),
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

        dim_event_cols = ["event_id", "event_name", "show_date", "venue_id"]
        if "primary_genre" in self._frames.dim_event.columns:
            dim_event_cols.append("primary_genre")
        latest = latest.merge(
            self._frames.dim_event[dim_event_cols],
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

    def genres(self) -> list[str]:
        """Distinct MUSIC genres present in the gold layer, sorted (dropdown feed).

        Non-music TM segments (sports, theatre, expos) are excluded here only —
        they remain in the warehouse and queryable via the agent.
        """
        if "primary_genre" not in self._latest.columns:
            return []
        values = self._latest["primary_genre"].dropna().astype(str)
        values = values[~values.isin(NON_MUSIC_GENRES)]
        return sorted({v for v in values if v.strip()})

    def search(
        self,
        *,
        q: str | None = None,
        genre: str | None = None,
        state: str | None = None,
        dma: str | None = None,
        max_price: float | None = None,
        days_ahead: int | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Filter the latest-snapshot summaries for the dashboard search panel.

        ``max_price`` filters on the projected price where one exists, falling back
        to the latest observed ``price_min`` (documented in the UI as "projected").
        ``days_ahead`` keeps upcoming shows within the window (and drops past ones).
        """
        df = self._latest
        if df.empty:
            return []

        mask = pd.Series(True, index=df.index)
        if q:
            needle = q.strip().lower()
            haystack = (
                df.reindex(columns=["event_name", "artist_name", "venue_name"])
                .fillna("")
                .astype(str)
                .apply(lambda col: col.str.lower())
            )
            mask &= haystack.apply(lambda col: col.str.contains(needle, regex=False)).any(axis=1)
        if genre and "primary_genre" in df.columns:
            mask &= df["primary_genre"].astype(str).str.lower() == genre.strip().lower()
        if state:
            mask &= df["state_code"].astype(str).str.upper() == state.strip().upper()
        if dma:
            mask &= df["dma_code"].astype(str) == str(dma).strip()
        if max_price is not None:
            effective = pd.to_numeric(df["forecast_price"], errors="coerce").fillna(
                pd.to_numeric(df["price_min"], errors="coerce")
            )
            mask &= effective.notna() & (effective <= max_price)
        if days_ahead is not None:
            today = pd.Timestamp.now().normalize()
            show = pd.to_datetime(df["show_date"], errors="coerce")
            mask &= (show >= today) & (show <= today + pd.Timedelta(days=days_ahead))

        return _records(df[mask].head(max(1, min(limit, 100))), SUMMARY_COLUMNS)

    def get_show(self, event_id: str) -> dict[str, Any] | None:
        """Return one show summary plus history and forecast series."""
        matches = self._latest[self._latest["event_id"] == event_id]
        if matches.empty:
            return None

        show = _records(matches.head(1), SUMMARY_COLUMNS)[0]
        show["history"] = self._history(event_id)
        show["history_filled"] = self._history_filled(event_id)
        show["forecast"] = self._forecast(event_id)
        return show

    def _history(self, event_id: str) -> list[dict[str, Any]]:
        fact = self._frames.fact[self._frames.fact["event_id"] == event_id].copy()
        if fact.empty:
            return []

        fact["snapshot_date"] = pd.to_datetime(fact["snapshot_date"], errors="coerce")
        fact = fact.sort_values("snapshot_date")
        return _records(fact, HISTORY_COLUMNS)

    def _history_filled(self, event_id: str) -> list[dict[str, Any]]:
        continuous = self._frames.continuous
        if continuous.empty or "event_id" not in continuous.columns:
            return []

        rows = continuous[continuous["event_id"] == event_id].copy()
        if rows.empty:
            return []

        rows["snapshot_date"] = pd.to_datetime(rows["snapshot_date"], errors="coerce")
        rows = rows.sort_values("snapshot_date")
        if "price_is_filled" in rows.columns:
            # BQ BOOL arrives as a nullable extension dtype that _to_numpy_backed maps
            # to float64 — coerce back so the API serves true booleans.
            rows["price_is_filled"] = (
                pd.to_numeric(rows["price_is_filled"], errors="coerce").fillna(0).astype(bool)
            )
        return _records(rows, HISTORY_FILLED_COLUMNS)

    def _forecast(self, event_id: str) -> list[dict[str, Any]]:
        forecast = self._frames.forecast[self._frames.forecast["event_id"] == event_id].copy()
        if forecast.empty:
            return []

        forecast["days_to_show"] = pd.to_numeric(forecast["days_to_show"], errors="coerce")
        forecast = forecast.sort_values("days_to_show", ascending=False)
        return _records(forecast, FORECAST_COLUMNS)


_repo: GoldRepository | None = None
_repo_lock = threading.Lock()


def set_repository(repository: GoldRepository | None) -> None:
    """Swap the process-wide repository; tests pass None to reset lazy loading."""
    global _repo
    with _repo_lock:
        _repo = repository


def get_repository() -> GoldRepository:
    """Return the process-wide repository, lazily loading gold data on first use.

    Thread-safe: the startup pre-warm thread (api/app.py lifespan) races the first
    HTTP requests, and the multi-minute BigQuery load must happen exactly once.
    """
    global _repo
    if _repo is None:
        with _repo_lock:
            if _repo is None:
                _repo = GoldRepository(load_gold_frames())
    return _repo
