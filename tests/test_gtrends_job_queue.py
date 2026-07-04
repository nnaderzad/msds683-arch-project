"""Unit tests for the daily-mode queue composition in google_trends_api/job.py.

Covers the 2026-07 collection redesign (docs/collection_efficiency_review.md D3):
tier-1 (Bay Area / EDM) per-DMA rotation and snapshot thinning in daily mode,
with backfill behavior unchanged. fetch_upcoming/build_frames are faked — no
network, no BigQuery.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

GTRENDS = Path(__file__).resolve().parent.parent / "google_trends_api"
sys.path.insert(0, str(GTRENDS))

import job  # noqa: E402
from build_roster import SF_DMA  # noqa: E402

TODAY_ORD = datetime.now(timezone.utc).date().toordinal()


def _fake_frames():
    """3 selected artists: EDM tourer, Bay-Area rock act, non-Bay non-EDM act."""
    artist = pd.DataFrame(
        {
            "artist": ["Acid Anna", "Bay Rockers", "Chicago Only"],
            "query": ["Acid Anna DJ", "Bay Rockers", "Chicago Only"],
            "soonest_date": ["2026-07-10", "2026-07-12", "2026-07-15"],
            "top_genre": ["Dance/Electronic", "Rock", "Rock"],
            "selected": [True, True, True],
        }
    )
    targets = pd.DataFrame(
        {
            "artist": ["Acid Anna", "Bay Rockers", "Bay Rockers", "Chicago Only"],
            "query": ["Acid Anna DJ", "Bay Rockers", "Bay Rockers", "Chicago Only"],
            "dma": ["803", SF_DMA, "751", "602"],
            "geo_code": ["US-CA-803", f"US-CA-{SF_DMA}", "US-CO-751", "US-IL-602"],
            "soonest_in_dma": ["2026-07-10", "2026-07-12", "2026-08-01", "2026-07-15"],
        }
    )
    return artist, targets, {"faked": True}


def _patched_queue(monkeypatch, **kwargs):
    monkeypatch.setattr(job, "fetch_upcoming", lambda states: [])
    monkeypatch.setattr(job, "build_frames", lambda rows, geo, top_n: _fake_frames())
    monkeypatch.setattr(job, "GeoLookup", lambda: None)
    return job.build_queue("daily" if "mode" not in kwargs else kwargs.pop("mode"),
                           top_n=250, states=None, **kwargs)


def test_cycle_hit_is_deterministic_and_covers_every_slot():
    # Exactly one hit per key across any N consecutive days, same day every cycle.
    for key in ("Acid Anna|803", "Bay Rockers|807", "solo-artist"):
        hits = [d for d in range(1000, 1008) if job.cycle_hit(key, 4, d)]
        assert len(hits) == 2 and hits[1] - hits[0] == 4
    # every_days <= 1 always hits.
    assert all(job.cycle_hit("k", 1, d) for d in range(5))
    assert all(job.cycle_hit("k", 0, d) for d in range(5))


def test_daily_tier1_rotation_targets_bay_and_edm_pairs_only(monkeypatch):
    units = _patched_queue(monkeypatch, daily_dma_refresh_days=1)  # 1 = every day
    dma_units = {(u["artist"], u["geo_code"]) for u in units if u["kind"] == "dma"}
    assert dma_units == {
        ("Acid Anna", "US-CA-803"),        # EDM artist -> all their metros
        ("Bay Rockers", f"US-CA-{SF_DMA}"),  # any artist's Bay Area pair
    }
    # Non-Bay pair of a non-EDM artist stays out (Bay Rockers @ Denver, Chicago Only).
    assert ("Bay Rockers", "US-CO-751") not in dma_units
    assert ("Chicago Only", "US-IL-602") not in dma_units


def test_daily_rotation_spreads_pairs_over_the_cycle(monkeypatch):
    units = _patched_queue(monkeypatch, daily_dma_refresh_days=4)
    expected = {
        pair for pair in (("Acid Anna", "US-CA-803"), ("Bay Rockers", f"US-CA-{SF_DMA}"))
        if job.cycle_hit(f"{pair[0]}|{pair[1].rsplit('-', 1)[-1]}", 4, TODAY_ORD)
    }
    got = {(u["artist"], u["geo_code"]) for u in units if u["kind"] == "dma"}
    assert got == expected


def test_daily_snapshot_thinning_rotates_the_roster(monkeypatch):
    units = _patched_queue(monkeypatch, snapshot_every_days=2)
    snapshot_artists = {u["artist"] for u in units if u["kind"] == "snapshot"}
    expected = {a for a in ("Acid Anna", "Bay Rockers", "Chicago Only")
                if job.cycle_hit(a, 2, TODAY_ORD)}
    assert snapshot_artists == expected
    # national stays daily for everyone regardless of thinning.
    assert {u["artist"] for u in units if u["kind"] == "national"} == {
        "Acid Anna", "Bay Rockers", "Chicago Only"}


def test_daily_defaults_and_backfill_are_unchanged(monkeypatch):
    # Defaults: no dma units in daily mode, snapshots every day.
    units = _patched_queue(monkeypatch)
    assert [u for u in units if u["kind"] == "dma"] == []
    assert sum(u["kind"] == "snapshot" for u in units) == 3

    # Backfill: all targets' dma units + all snapshots, thinning ignored.
    units = _patched_queue(monkeypatch, mode="backfill", include_dma=True,
                           daily_dma_refresh_days=4, snapshot_every_days=2)
    assert sum(u["kind"] == "dma" for u in units) == 4
    assert sum(u["kind"] == "snapshot" for u in units) == 3
