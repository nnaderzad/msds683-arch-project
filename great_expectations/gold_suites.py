"""C4 — gold GX suite: `fact_event_demand` integrity.

The gold star is the `fact_ticketmaster` event×snapshot spine with the other source
signals LEFT-JOINed on the **headliner** artist + the event's venue DMA (NULL where a
signal wasn't collected). The dbt model (B1) builds it in BigQuery; here we replicate
that join in pandas over the C3 seed-built silver so the integrity checks run offline
in CI. The same suite can validate the live gold table via the wired BQ datasource.

Invariants (docs/data-model.md §4 + B1's singular test):
  * **No row drop** — gold rows == spine rows (LEFT JOINs never drop or fan out the
    spine; the silver PKs guarantee no fan-out).
  * Grain unique on (event_id, snapshot_date).
  * Non-negative `price_min`/`price_max`; `local_interest` ∈ [0,100] when present;
    `yt_*` ≥ 0 when present (all nullable — LEFT JOIN).
  * `days_to_show` monotonic toward the show date (checked per-event in the tests).
  * FK integrity back to the conformed dims.
"""

from __future__ import annotations

import pandas as pd

import great_expectations as gx
import silver_suites  # C3: build_silver_frames()

GOLD_TABLES = ["fact_event_demand"]


def _date_str(series) -> pd.Series:
    """Normalize any date/datetime/string column to 'YYYY-MM-DD' for joining."""
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")


def build_gold_frame(frames: dict | None = None):
    """Replicate the dbt gold join over seed-built silver. Returns (gold_df, spine_rows)."""
    if frames is None:
        frames = silver_suites.build_silver_frames()

    tm = frames["fact_ticketmaster"]
    spine = pd.DataFrame({
        "event_id": tm["event_id"],
        "snapshot_date": _date_str(tm["snapshot_date"]),
        "price_min": pd.to_numeric(tm["price_min"], errors="coerce"),
        "price_max": pd.to_numeric(tm["price_max"], errors="coerce"),
        "price_currency": tm["price_currency"],
        "status_code": tm["status_code"],
    })
    spine["days_to_show"] = (pd.to_datetime(_date_str(tm["local_date"]))
                             - pd.to_datetime(spine["snapshot_date"])).dt.days

    # One headliner per event (dedupe defensively so the spine can't fan out).
    bridge = frames["bridge_event_artist"]
    headliner = (bridge[bridge["is_headliner"]]
                 .sort_values(["event_id", "billing_order", "artist_id"])
                 .drop_duplicates("event_id")[["event_id", "artist_id"]])

    # Venue + DMA from the conformed dims (matches the dbt event_venue CTE).
    event_venue = (frames["dim_event"][["event_id", "venue_id"]]
                   .merge(frames["dim_venue"][["venue_id", "dma_code"]], on="venue_id", how="left"))

    trends = (frames["fact_trends"][["artist_id", "dma_code", "snapshot_date", "interest"]]
              .rename(columns={"interest": "local_interest"}).copy())
    trends["snapshot_date"] = _date_str(trends["snapshot_date"])

    youtube = (frames["fact_youtube"][["artist_id", "snapshot_date",
                                       "official_subscribers", "official_total_views"]]
               .rename(columns={"official_subscribers": "yt_subscribers",
                                "official_total_views": "yt_views"}).copy())
    youtube["snapshot_date"] = _date_str(youtube["snapshot_date"])

    gold = (spine
            .merge(headliner, on="event_id", how="left")
            .merge(event_venue, on="event_id", how="left")
            .merge(trends, on=["artist_id", "dma_code", "snapshot_date"], how="left")
            .merge(youtube, on=["artist_id", "snapshot_date"], how="left"))
    return gold, len(spine)


def build_gold_suite(frames: dict, spine_rows: int) -> gx.ExpectationSuite:
    s = gx.ExpectationSuite(name="gold_fact_event_demand")
    # No-row-drop: every spine row survives, none duplicated.
    s.add_expectation(gx.expectations.ExpectTableRowCountToEqual(value=spine_rows))
    s.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="event_id"))
    s.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="snapshot_date"))
    s.add_expectation(gx.expectations.ExpectCompoundColumnsToBeUnique(
        column_list=["event_id", "snapshot_date"]))
    # Prices non-negative (NULL when unpriced).
    s.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="price_min", min_value=0))
    s.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="price_max", min_value=0))
    # Joined-in signals: nullable, but in-range / non-negative when present.
    s.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(
        column="local_interest", min_value=0, max_value=100))
    s.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="yt_subscribers", min_value=0))
    s.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="yt_views", min_value=0))
    # FK integrity back to the dims (nullable where a LEFT JOIN found no match).
    s.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(
        column="event_id", value_set=frames["dim_event"]["event_id"].dropna().unique().tolist()))
    s.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(
        column="artist_id", value_set=frames["dim_artist"]["artist_id"].dropna().unique().tolist()))
    s.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(
        column="venue_id", value_set=frames["dim_venue"]["venue_id"].dropna().unique().tolist()))
    return s


def run_gold_offline(project_root=None) -> dict:
    """Build gold from seed-built silver and validate its integrity. {table: result}."""
    import gx_project

    context = gx_project.get_context(project_root)   # active context before add_expectation
    frames = silver_suites.build_silver_frames()
    gold, spine_rows = build_gold_frame(frames)
    suite = build_gold_suite(frames, spine_rows)
    result = gx_project.run_suite_on_dataframe(context, gold, suite, key="gold_fact_event_demand")
    return {"fact_event_demand": result}
