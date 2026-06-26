"""Offline tests for A1 — Trends bronze -> silver (fact_trends).

Pure-transform + deterministic-key unit tests, plus a real run of the transform over
the committed T0 seed fixtures (read from disk, no network — BigQuery I/O is isolated
in upsert_to_silver and never touched here).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))                                   # common.keys
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "silver"))          # trends_to_silver

import trends_to_silver as t  # noqa: E402
from common import keys  # noqa: E402

SEED_TRENDS = REPO_ROOT / "tests" / "fixtures" / "seed" / "google_trends"
LOAD_TS = "2026-06-26T00:00:00+00:00"


def _ibr(artist="Bingo Loco", records=None):
    return {
        "source": "google_trends", "endpoint": "interest_by_region",
        "artist": artist, "query": artist, "geo": "US", "geo_code": None,
        "resolution": "DMA", "granularity": "snapshot", "timeframe": "today 12-m",
        "n_records": len(records or []), "records": records or [],
    }


# ---------------------------------------------------------------------------
# Deterministic keys
# ---------------------------------------------------------------------------

def test_artist_id_is_deterministic_and_normalized():
    assert keys.artist_id("Bingo Loco") == keys.artist_id("  bingo   LOCO ")
    assert keys.artist_id("Bingo Loco") != keys.artist_id("Everclear")
    assert 0 < keys.artist_id("Bingo Loco") < (1 << 63)


def test_snapshot_id_is_stable_and_distinct():
    a = keys.snapshot_id("trends", 1, "807", "2026-06-25")
    assert a == keys.snapshot_id("trends", 1, "807", "2026-06-25")  # stable
    assert a != keys.snapshot_id("trends", 1, "807", "2026-06-24")  # date matters
    assert a != keys.snapshot_id("trends", 1, "535", "2026-06-25")  # dma matters


# ---------------------------------------------------------------------------
# Pure transform
# ---------------------------------------------------------------------------

def test_national_payload_yields_no_rows():
    national = {"endpoint": "interest_over_time", "resolution": "national",
                "artist": "Bingo Loco", "query": "Bingo Loco", "records": [{"date": "x"}]}
    assert t.snapshot_payload_to_rows(national, "2026-06-25", LOAD_TS) == []


def test_ibr_payload_flattens_to_one_row_per_dma():
    payload = _ibr(records=[
        {"geoName": "San Francisco-Oakland-San Jose CA", "geoCode": "807", "Bingo Loco": 73},
        {"geoName": "Columbus OH", "geoCode": "535", "Bingo Loco": 0},
        {"geoName": "Nowhere", "geoCode": "", "Bingo Loco": 5},   # no DMA -> dropped
        {"geoName": "Missing", "geoCode": "999", "Bingo Loco": None},  # no interest -> dropped
    ])
    rows = t.snapshot_payload_to_rows(payload, "2026-06-25", LOAD_TS)
    assert len(rows) == 2
    by_dma = {r["dma_code"]: r for r in rows}
    assert set(by_dma) == {"807", "535"}
    row = by_dma["807"]
    assert row["artist_id"] == keys.artist_id("Bingo Loco")
    assert row["interest"] == 73
    assert row["snapshot_date"] == "2026-06-25"
    assert row["granularity"] == "snapshot" and row["is_partial"] is False
    assert row["trends_snapshot_id"] == keys.snapshot_id(
        "trends", keys.artist_id("Bingo Loco"), "807", "2026-06-25")
    assert set(row) == {c for c, _ in t.STAGING_SCHEMA}


def test_dedupe_keeps_one_row_per_snapshot_id():
    payload = _ibr(records=[{"geoName": "SF", "geoCode": "807", "Bingo Loco": 10}])
    dup = t.rows_from_payloads([("2026-06-25", payload), ("2026-06-25", payload)], LOAD_TS)
    assert len(dup) == 2 and len(t.dedupe_rows(dup)) == 1


# ---------------------------------------------------------------------------
# Real run over the committed seed fixtures (offline)
# ---------------------------------------------------------------------------

def test_transform_over_seed_fixtures(seed_artists):
    payloads = t.read_fixture_dir(str(SEED_TRENDS))
    assert payloads, "seed google_trends fixtures missing"
    rows = t.dedupe_rows(t.rows_from_payloads(payloads, LOAD_TS))

    assert rows
    # grain is unique on (artist, dma, snapshot_date)
    grain = {(r["artist_id"], r["dma_code"], r["snapshot_date"]) for r in rows}
    assert len(grain) == len(rows)
    # value range + shape invariants the GX silver suite (C3) will formalize
    for r in rows:
        assert 0 <= r["interest"] <= 100
        assert r["dma_code"] and r["granularity"] == "snapshot"
    # national (iot_US) files in the same dir contribute nothing
    assert all(r["snapshot_date"] for r in rows)
    # every seed artist with a DMA snapshot is represented, keyed deterministically
    seed_ids = {keys.artist_id(a) for a in seed_artists}
    assert {r["artist_id"] for r in rows} <= seed_ids
    assert {r["artist_id"] for r in rows} == seed_ids  # all 5 have ibr snapshots
