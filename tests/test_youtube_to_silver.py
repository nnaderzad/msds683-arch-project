"""Offline tests for A2 — YouTube bronze -> silver (fact_youtube).

Pure-transform + deterministic-key units, plus a real run of the transform over the
committed T0 seed fixtures (read from disk, no network — BigQuery I/O is isolated in
upsert_to_silver and never touched here).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))                                   # common.keys
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "silver"))          # youtube_to_silver

import youtube_to_silver as y  # noqa: E402
from common import keys  # noqa: E402

SEED_YT = REPO_ROOT / "tests" / "fixtures" / "seed" / "youtube"
LOAD_TS = "2026-06-26T00:00:00+00:00"


def _rec(query="Everclear", subs=1000, hidden=False):
    return {
        "query": query,
        "official_channel_id": "UC123", "official_channel_title": query,
        "official_subscribers": None if hidden else subs,
        "official_total_views": 5000, "official_video_count": 42,
        "topic_channel_id": "UC456", "topic_channel_title": f"{query} - Topic",
        "topic_total_views": 9000, "topic_video_count": 7,
    }


def _payload(records):
    return {"source": "youtube", "extract_ts_utc": "2026-06-25T16:31:42+00:00",
            "n_records": len(records), "records": records}


# ---------------------------------------------------------------------------
# Pure transform
# ---------------------------------------------------------------------------

def test_payload_flattens_one_row_per_artist():
    rows = y.payload_to_rows(_payload([_rec("Everclear"), _rec("CAIN")]), "2026-06-25", LOAD_TS)
    assert len(rows) == 2
    row = next(r for r in rows if r["artist_id"] == keys.artist_id("Everclear"))
    assert row["snapshot_date"] == "2026-06-25"
    assert row["official_subscribers"] == 1000
    assert row["official_video_count"] == 42 and row["topic_total_views"] == 9000
    assert row["youtube_snapshot_id"] == keys.snapshot_id(
        "youtube", keys.artist_id("Everclear"), "2026-06-25")
    assert set(row) == {c for c, _ in y.STAGING_SCHEMA}
    # channel ids are NOT carried on the fact (they belong on dim_artist)
    assert "official_channel_id" not in row


def test_hidden_subscriber_count_stays_null():
    [row] = y.payload_to_rows(_payload([_rec("Everclear", hidden=True)]), "2026-06-25", LOAD_TS)
    assert row["official_subscribers"] is None
    assert row["official_total_views"] == 5000  # other measures still populated


def test_record_without_query_is_skipped():
    bad = {"official_subscribers": 10}  # no query -> no artist key
    assert y.payload_to_rows(_payload([bad]), "2026-06-25", LOAD_TS) == []


def test_dedupe_collapses_multiple_runs_in_a_day():
    p = _payload([_rec("Everclear", subs=1000)])
    p2 = _payload([_rec("Everclear", subs=1234)])  # later run, same day
    rows = y.rows_from_payloads([("2026-06-25", p), ("2026-06-25", p2)], LOAD_TS)
    deduped = y.dedupe_rows(rows)
    assert len(rows) == 2 and len(deduped) == 1
    assert deduped[0]["official_subscribers"] == 1234  # last wins


# ---------------------------------------------------------------------------
# Real run over the committed seed fixtures (offline)
# ---------------------------------------------------------------------------

def test_transform_over_seed_fixtures(seed_artists):
    payloads = y.read_fixture_dir(str(SEED_YT))
    assert payloads, "seed youtube fixtures missing"
    rows = y.dedupe_rows(y.rows_from_payloads(payloads, LOAD_TS))

    assert rows
    # grain is unique on (artist, snapshot_date)
    grain = {(r["artist_id"], r["snapshot_date"]) for r in rows}
    assert len(grain) == len(rows)
    # every seed artist is represented, keyed deterministically
    assert {r["artist_id"] for r in rows} == {keys.artist_id(a) for a in seed_artists}
    # subscriber counts are ints or NULL, never negative
    for r in rows:
        subs = r["official_subscribers"]
        assert subs is None or (isinstance(subs, int) and subs >= 0)
