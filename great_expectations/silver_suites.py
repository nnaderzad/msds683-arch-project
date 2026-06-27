"""C3 — silver GX suites (schema, nulls, value ranges, join-key integrity).

Validates the conformed silver layer — the three native-grain facts and the
conformed dims/bridge — for schema, null/uniqueness of keys, value ranges
(`interest` ∈ [0,100]), and join-key (FK) integrity.

Offline-first like C2: the silver tables are rebuilt from the committed seed using
the *same* A1/A2/A3 transform code (`trends_to_silver`, `youtube_to_silver`,
`build_dimensions`), so CI validates real transform output with no warehouse. The
same suites can run against the live BigQuery tables via `gx_project`'s wired BQ
datasource (ADC). Schema-existence checks are driven off the transforms' own schema
constants, so they can't drift from the code that writes the tables.

Notes:
  * `dim_date` is built with an empty holiday set — holiday *correctness* is A3's
    concern; C3 only validates the column's shape/type.
  * `fact_ticketmaster` is a dbt model (A4) sourced from processed parquet, so it
    can't be rebuilt offline here; we validate the seed TM price panel
    (`tm_events_seed.csv`) as its stand-in. The dbt model carries its own dbt tests,
    and the BQ datasource validates it live.
  * fact→dim FKs are validated on the seed (which guarantees cross-source artists);
    on full data gold resolves unmatched signals via LEFT JOIN, so a live run may
    use a tolerance.
"""

from __future__ import annotations

import csv
import sys

import pandas as pd

import great_expectations as gx
from gx_project import REPO_ROOT

# Silver transforms live outside this dir — import them the way the A-task tests do.
for _p in ("pipeline/silver", "google_trends_api"):
    sys.path.insert(0, str(REPO_ROOT / _p))
sys.path.insert(0, str(REPO_ROOT))

import build_dimensions as D  # noqa: E402
import trends_to_silver as T  # noqa: E402
import youtube_to_silver as Y  # noqa: E402
from geo_lookup import GeoLookup  # noqa: E402

SEED = REPO_ROOT / "tests" / "fixtures" / "seed"
LOAD_TS = "2026-06-27T00:00:00Z"


# ---------------------------------------------------------------------------
# Build the silver tables from the seed (reusing A1/A2/A3 transforms)
# ---------------------------------------------------------------------------

def build_silver_frames() -> dict[str, pd.DataFrame]:
    """Return {table_name: dataframe} for every silver table, built from the seed."""
    geo = GeoLookup()
    with open(SEED / "ticketmaster" / "tm_events_seed.csv") as fh:
        tm_rows = list(csv.DictReader(fh))
    start, end = D.date_span(tm_rows)

    return {
        "fact_trends": pd.DataFrame(
            T.dedupe_rows(T.rows_from_payloads(T.read_fixture_dir(str(SEED / "google_trends")), LOAD_TS))
        ),
        "fact_youtube": pd.DataFrame(
            Y.dedupe_rows(Y.rows_from_payloads(Y.read_fixture_dir(str(SEED / "youtube")), LOAD_TS))
        ),
        "dim_geo": pd.DataFrame(D.build_dim_geo(D.read_reference_geo())),
        "dim_date": pd.DataFrame(D.build_dim_date(start, end, set())),
        "dim_venue": pd.DataFrame(D.build_dim_venue(tm_rows, geo)),
        "dim_artist": pd.DataFrame(D.build_dim_artist(tm_rows, {}, {})),
        "dim_event": pd.DataFrame(D.build_dim_event(tm_rows)),
        "bridge_event_artist": pd.DataFrame(D.build_bridge_event_artist(tm_rows)),
        "fact_ticketmaster": pd.read_csv(SEED / "ticketmaster" / "tm_events_seed.csv"),
    }


# ---------------------------------------------------------------------------
# Expectation helpers
# ---------------------------------------------------------------------------

def _exist(suite, columns):
    for col in columns:
        suite.add_expectation(gx.expectations.ExpectColumnToExist(column=col))


def _not_null(suite, *columns):
    for col in columns:
        suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column=col))


def _unique(suite, column):
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeUnique(column=column))


def _compound_unique(suite, columns):
    suite.add_expectation(gx.expectations.ExpectCompoundColumnsToBeUnique(column_list=list(columns)))


def _between(suite, column, min_value=None, max_value=None):
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeBetween(column=column, min_value=min_value, max_value=max_value)
    )


def _in_set(suite, column, value_set):
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column=column, value_set=list(value_set)))


def _fk(suite, column, parent_df, parent_col):
    """Foreign-key integrity: every non-null child value exists in the parent column."""
    _in_set(suite, column, parent_df[parent_col].dropna().unique().tolist())


# ---------------------------------------------------------------------------
# Per-table suites
# ---------------------------------------------------------------------------

def build_suites(frames: dict[str, pd.DataFrame]) -> dict[str, gx.ExpectationSuite]:
    suites: dict[str, gx.ExpectationSuite] = {}

    # --- fact_trends (artist × dma × snapshot_date) ---
    s = gx.ExpectationSuite(name="silver_fact_trends")
    _exist(s, [c for c, _ in T.STAGING_SCHEMA])
    _not_null(s, "trends_snapshot_id", "artist_id", "dma_code", "snapshot_date", "interest")
    _unique(s, "trends_snapshot_id")
    _compound_unique(s, ["artist_id", "dma_code", "snapshot_date"])
    _between(s, "interest", 0, 100)            # the per-pull 0–100 discipline
    _in_set(s, "is_partial", [True, False])
    _fk(s, "dma_code", frames["dim_geo"], "dma_code")
    _fk(s, "artist_id", frames["dim_artist"], "artist_id")
    suites["fact_trends"] = s

    # --- fact_youtube (artist × snapshot_date) ---
    s = gx.ExpectationSuite(name="silver_fact_youtube")
    _exist(s, [c for c, _ in Y.STAGING_SCHEMA])
    _not_null(s, "youtube_snapshot_id", "artist_id", "snapshot_date")
    _unique(s, "youtube_snapshot_id")
    _compound_unique(s, ["artist_id", "snapshot_date"])
    for measure in ("official_subscribers", "official_total_views", "official_video_count",
                    "topic_total_views", "topic_video_count"):
        _between(s, measure, min_value=0)      # counts non-negative; NULL allowed (hidden)
    _fk(s, "artist_id", frames["dim_artist"], "artist_id")
    suites["fact_youtube"] = s

    # --- dim_geo ---
    s = gx.ExpectationSuite(name="silver_dim_geo")
    _exist(s, [c for c, _ in D.SCHEMAS["dim_geo"]])
    _not_null(s, "dma_code")
    _unique(s, "dma_code")
    suites["dim_geo"] = s

    # --- dim_date ---
    s = gx.ExpectationSuite(name="silver_dim_date")
    _exist(s, [c for c, _ in D.SCHEMAS["dim_date"]])
    _not_null(s, "date")
    _unique(s, "date")
    _between(s, "month", 1, 12)
    _between(s, "quarter", 1, 4)
    _between(s, "day_of_week", 1, 7)   # ISO weekday: Mon=1 … Sun=7
    _in_set(s, "is_weekend", [True, False])
    _in_set(s, "is_us_holiday", [True, False])
    suites["dim_date"] = s

    # --- dim_venue ---
    s = gx.ExpectationSuite(name="silver_dim_venue")
    _exist(s, [c for c, _ in D.SCHEMAS["dim_venue"]])
    _not_null(s, "venue_id")
    _unique(s, "venue_id")
    _between(s, "latitude", -90, 90)
    _between(s, "longitude", -180, 180)
    _fk(s, "dma_code", frames["dim_geo"], "dma_code")   # nulls (~unresolved) ignored
    suites["dim_venue"] = s

    # --- dim_artist ---
    s = gx.ExpectationSuite(name="silver_dim_artist")
    _exist(s, [c for c, _ in D.SCHEMAS["dim_artist"]])
    _not_null(s, "artist_id", "artist_name")
    _unique(s, "artist_id")
    suites["dim_artist"] = s

    # --- dim_event ---
    s = gx.ExpectationSuite(name="silver_dim_event")
    _exist(s, [c for c, _ in D.SCHEMAS["dim_event"]])
    _not_null(s, "event_id")
    _unique(s, "event_id")
    _fk(s, "venue_id", frames["dim_venue"], "venue_id")
    suites["dim_event"] = s

    # --- bridge_event_artist ---
    s = gx.ExpectationSuite(name="silver_bridge_event_artist")
    _exist(s, [c for c, _ in D.SCHEMAS["bridge_event_artist"]])
    _not_null(s, "event_id", "artist_id")
    _compound_unique(s, ["event_id", "artist_id"])
    _in_set(s, "is_headliner", [True, False])
    _fk(s, "artist_id", frames["dim_artist"], "artist_id")
    _fk(s, "event_id", frames["dim_event"], "event_id")
    suites["bridge_event_artist"] = s

    # --- fact_ticketmaster (seed TM price panel: event × snapshot_date) ---
    s = gx.ExpectationSuite(name="silver_fact_ticketmaster")
    _not_null(s, "event_id", "snapshot_date")
    _compound_unique(s, ["event_id", "snapshot_date"])
    _between(s, "price_min", min_value=0)               # face value, non-negative; NULL when unpriced
    _between(s, "price_max", min_value=0)
    s.add_expectation(gx.expectations.ExpectColumnPairValuesAToBeGreaterThanB(
        column_A="price_max", column_B="price_min", or_equal=True))
    suites["fact_ticketmaster"] = s

    return suites


# Stable order for listing / running.
SILVER_TABLES = [
    "fact_trends", "fact_youtube", "fact_ticketmaster",
    "dim_geo", "dim_date", "dim_venue", "dim_artist", "dim_event", "bridge_event_artist",
]


def run_silver_offline(project_root=None) -> dict:
    """Run every silver suite over seed-built tables. Returns {table: result}."""
    import gx_project

    # Context must be active before building suites (GX 1.x add_expectation needs it).
    context = gx_project.get_context(project_root)
    frames = build_silver_frames()
    suites = build_suites(frames)
    results = {}
    for table in SILVER_TABLES:
        results[table] = gx_project.run_suite_on_dataframe(
            context, frames[table], suites[table], key=f"silver_{table}"
        )
    return results
