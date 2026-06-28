"""Offline tests for the iot per-DMA daily transform (fact_trends_daily).

Pure-transform unit tests only — BigQuery/GCS I/O is isolated in read_bronze /
upsert_to_silver and never touched here.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))                                   # common.keys
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "silver"))          # trends_series_to_silver

import trends_series_to_silver as t  # noqa: E402
from common import keys  # noqa: E402

LOAD_TS = "2026-06-26T00:00:00+00:00"


def _iot(artist="Fisher", query="Fisher DJ", geo_code="US-CA-807", records=None):
    """An interest_over_time per-DMA (dma_series) payload, as the collector lands it."""
    return {
        "source": "google_trends", "endpoint": "interest_over_time",
        "artist": artist, "query": query, "geo": geo_code, "geo_code": geo_code,
        "resolution": "dma_series", "granularity": "daily",
        "timeframe": "2025-09-18 2026-06-14",
        "n_records": len(records or []), "records": records or [],
    }


def _rec(date, value, query="Fisher DJ", is_partial=False):
    return {"date": f"{date}T00:00:00.000Z", query: value, "isPartial": is_partial}


def test_bare_dma_extraction():
    assert t.bare_dma("US-CA-807") == "807"
    assert t.bare_dma("US-NY-501") == "501"
    assert t.bare_dma("807") == "807"
    assert t.bare_dma("") == ""
    assert t.bare_dma(None) == ""


def test_series_payload_flattens_daily_records():
    payload = _iot(records=[_rec("2026-06-12", 40), _rec("2026-06-13", 55, is_partial=True)])
    rows = t.series_payload_to_rows(payload, capture_dt="2026-06-14", load_ts=LOAD_TS)
    by_date = {r["snapshot_date"]: r for r in rows}
    assert set(by_date) == {"2026-06-12", "2026-06-13"}
    r = by_date["2026-06-12"]
    assert r["artist_id"] == keys.artist_id("Fisher")
    assert r["dma_code"] == "807"                 # bare DMA, not US-CA-807
    assert r["interest"] == 40
    assert r["granularity"] == "daily"
    assert r["is_partial"] is False
    assert by_date["2026-06-13"]["is_partial"] is True
    # deterministic surrogate keyed on the OBSERVATION day
    assert r["trends_snapshot_id"] == keys.snapshot_id(
        "trends", keys.artist_id("Fisher"), "807", "2026-06-12")


def test_series_payload_skips_null_interest():
    payload = _iot(records=[_rec("2026-06-12", None), _rec("2026-06-13", 0)])
    rows = t.series_payload_to_rows(payload, capture_dt="2026-06-14", load_ts=LOAD_TS)
    # null interest dropped; an explicit 0 is kept (a real low-interest day)
    assert [r["snapshot_date"] for r in rows] == ["2026-06-13"]
    assert rows[0]["interest"] == 0


def test_non_dma_series_payloads_ignored():
    # national iot (resolution=national) and the ibr snapshot are NOT this fact's source.
    national = _iot(geo_code="US", records=[_rec("2026-06-12", 30)])
    national["resolution"] = "national"
    national["geo_code"] = "US"
    ibr = {"endpoint": "interest_by_region", "resolution": "DMA", "artist": "Fisher",
           "query": "Fisher DJ", "records": [{"geoCode": "807", "Fisher DJ": 50}]}
    assert t.series_payload_to_rows(national, "2026-06-14", LOAD_TS) == []
    assert t.series_payload_to_rows(ibr, "2026-06-14", LOAD_TS) == []


def test_dedupe_keeps_latest_capture():
    # Same (artist, DMA, day) collected on two capture days with different values.
    old = t.series_payload_to_rows(_iot(records=[_rec("2026-06-12", 40)]),
                                   capture_dt="2026-06-14", load_ts=LOAD_TS)
    new = t.series_payload_to_rows(_iot(records=[_rec("2026-06-12", 90)]),
                                   capture_dt="2026-06-16", load_ts=LOAD_TS)
    deduped = t.dedupe_latest(old + new)
    assert len(deduped) == 1
    assert deduped[0]["interest"] == 90              # latest capture wins
    assert "_capture_dt" not in deduped[0]           # helper key stripped before load
