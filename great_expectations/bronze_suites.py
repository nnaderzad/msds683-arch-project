"""C2 — bronze raw-JSON landing checks (one suite per source).

Validates the *shape* of what each collector lands in the bronze bucket — required
keys present, correct value ranges, non-empty — before silver ever reads it. Built
on the C1 scaffold (`gx_project.run_suite_on_dataframe`) and exercised offline in CI
against the committed seed fixtures; the same extractors point at a downloaded GCS
prefix when validating live bronze with ADC.

Each source's raw records are flattened into a dataframe so GX column expectations
apply. The Google Trends "interest" value arrives under a column named after the
*query* (e.g. ``{"Bingo Loco": 20}``); the extractor normalizes it to ``interest``.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pandas as pd

import great_expectations as gx
from gx_project import REPO_ROOT

SEED_BRONZE = REPO_ROOT / "tests" / "fixtures" / "seed"


# ---------------------------------------------------------------------------
# Extractors: raw bronze JSON -> one dataframe of records per source
# ---------------------------------------------------------------------------

def _read_json(path: str | Path):
    with open(path) as fh:
        return json.load(fh)


def load_ticketmaster_events(base: str | Path = SEED_BRONZE) -> pd.DataFrame:
    """Flatten Ticketmaster bronze event objects into a per-event dataframe.

    Handles both the trimmed seed (a top-level list of events) and live landings
    (the API page response with ``_embedded.events``).
    """
    rows = []
    for path in sorted(glob.glob(f"{base}/ticketmaster/dt=*/*.json")):
        payload = _read_json(path)
        events = payload if isinstance(payload, list) else (
            payload.get("_embedded", {}).get("events") or payload.get("events") or []
        )
        for e in events:
            rows.append({
                "event_id": e.get("id"),
                "event_name": e.get("name"),
                "has_dates": bool(e.get("dates")),
                "has_embedded": bool(e.get("_embedded")),
                "has_classifications": bool(e.get("classifications")),
                "n_price_ranges": len(e.get("priceRanges") or []),
            })
    return pd.DataFrame(rows)


def _load_trends(base: str | Path, file_glob: str, *, national: bool) -> pd.DataFrame:
    rows = []
    for path in sorted(glob.glob(f"{base}/google_trends/dt=*/{file_glob}")):
        payload = _read_json(path)
        query = payload.get("query")
        artist = payload.get("artist")
        for rec in payload.get("records", []):
            row = {"artist": artist, "query": query, "interest": rec.get(query)}
            if national:
                row.update({"date": rec.get("date"), "is_partial": rec.get("isPartial")})
            else:
                row.update({"geo_name": rec.get("geoName"), "geo_code": rec.get("geoCode")})
            rows.append(row)
    return pd.DataFrame(rows)


def load_trends_national(base: str | Path = SEED_BRONZE) -> pd.DataFrame:
    """interest_over_time (national) records → artist × date × interest."""
    return _load_trends(base, "google_trends_iot_US_*.json", national=True)


def load_trends_dma(base: str | Path = SEED_BRONZE) -> pd.DataFrame:
    """interest_by_region (DMA) records → artist × dma × interest."""
    return _load_trends(base, "google_trends_ibr_DMA_*.json", national=False)


def load_youtube_records(base: str | Path = SEED_BRONZE) -> pd.DataFrame:
    """Flatten YouTube daily payload records into a per-artist dataframe."""
    rows = []
    for path in sorted(glob.glob(f"{base}/youtube/dt=*/youtube_*.json")):
        payload = _read_json(path)
        for rec in payload.get("records", []):
            rows.append({
                "query": rec.get("query"),
                "official_channel_id": rec.get("official_channel_id"),
                "official_subscribers": rec.get("official_subscribers"),
                "official_total_views": rec.get("official_total_views"),
                "official_video_count": rec.get("official_video_count"),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Suites: required keys, value ranges, non-empty
# ---------------------------------------------------------------------------

def _exists_and_not_null(suite, *columns):
    for col in columns:
        suite.add_expectation(gx.expectations.ExpectColumnToExist(column=col))
        suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column=col))


def build_ticketmaster_bronze_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="bronze_ticketmaster")
    suite.add_expectation(gx.expectations.ExpectTableRowCountToBeBetween(min_value=1))
    _exists_and_not_null(suite, "event_id", "event_name")
    # Every landed event must carry the nested blocks silver flattens.
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column="has_dates", value_set=[True]))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column="has_embedded", value_set=[True]))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="n_price_ranges", min_value=0))
    return suite


def build_trends_national_bronze_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="bronze_trends_national")
    suite.add_expectation(gx.expectations.ExpectTableRowCountToBeBetween(min_value=1))
    _exists_and_not_null(suite, "artist", "query", "date")
    suite.add_expectation(gx.expectations.ExpectColumnToExist(column="interest"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="interest", min_value=0, max_value=100))
    return suite


def build_trends_dma_bronze_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="bronze_trends_dma")
    suite.add_expectation(gx.expectations.ExpectTableRowCountToBeBetween(min_value=1))
    _exists_and_not_null(suite, "artist", "query", "geo_code")
    suite.add_expectation(gx.expectations.ExpectColumnToExist(column="interest"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="interest", min_value=0, max_value=100))
    return suite


def build_youtube_bronze_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="bronze_youtube")
    suite.add_expectation(gx.expectations.ExpectTableRowCountToBeBetween(min_value=1))
    _exists_and_not_null(suite, "query", "official_channel_id")
    suite.add_expectation(gx.expectations.ExpectColumnToExist(column="official_total_views"))
    # Counts are non-negative; subscribers may be hidden (NULL) — between ignores nulls.
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="official_total_views", min_value=0))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="official_video_count", min_value=0))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="official_subscribers", min_value=0))
    return suite


# Source key -> (extractor, suite builder). The key namespaces GX resources and is
# the bronze checkpoint name (minus a prefix) for `run_checkpoints.py --list`.
BRONZE_SOURCES = {
    "bronze_ticketmaster": (load_ticketmaster_events, build_ticketmaster_bronze_suite),
    "bronze_trends_national": (load_trends_national, build_trends_national_bronze_suite),
    "bronze_trends_dma": (load_trends_dma, build_trends_dma_bronze_suite),
    "bronze_youtube": (load_youtube_records, build_youtube_bronze_suite),
}


def run_bronze_offline(project_root=None, base: str | Path = SEED_BRONZE) -> dict:
    """Run every bronze suite over the seed fixtures. Returns {key: result}."""
    import gx_project

    context = gx_project.get_context(project_root)
    results = {}
    for key, (loader, build_suite) in BRONZE_SOURCES.items():
        df = loader(base)
        results[key] = gx_project.run_suite_on_dataframe(context, df, build_suite(), key=key)
    return results
