"""Offline tests for the cross-source seed slice (task T0).

Two halves:
  * unit tests for build_seed.py's pure selection helpers (no network), and
  * validation that the committed fixtures under tests/fixtures/seed/ are real,
    self-consistent, and — the property every silver task relies on — that each
    seed artist is present in ALL THREE sources.

Everything here is pure stdlib + committed files; nothing touches GCP, so it runs
in CI exactly like the rest of tests/.
"""

from __future__ import annotations

import sys
from pathlib import Path

# build_seed lives next to the fixtures it generates.
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import build_seed as bs  # noqa: E402

COMPLETE_PRICE = 0.9  # mirrors tm_price_eda.COMPLETE_PRICE (the "complete" threshold)


# ---------------------------------------------------------------------------
# Pure selection helpers
# ---------------------------------------------------------------------------

def test_normalize_collapses_space_and_case():
    assert bs.normalize("  Alison   KRAUSS ") == "alison krauss"
    assert bs.normalize(None) == ""


def test_trends_slug_matches_landing_contract():
    assert bs.trends_slug("Alison Krauss and Union Station") == "Alison-Krauss-and-Union-Station"
    assert bs.trends_slug("5 Seconds of Summer") == "5-Seconds-of-Summer"
    assert bs.trends_slug("") == "x"


def test_parse_trends_slug_sets_splits_kinds():
    national, snapshot = bs.parse_trends_slug_sets([
        "google_trends_iot_US_Chicago_20260625T163010Z.json",
        "google_trends_ibr_DMA_Chicago_20260625T163010Z.json",
        "google_trends_iot_US-CA-807_Chicago_20260625T163010Z.json",  # dma series: ignored
        "google_trends_iot_US_Turnover_20260624T164916Z.json",
    ])
    assert national == {"Chicago", "Turnover"}
    assert snapshot == {"Chicago"}


def test_cross_source_requires_all_three_signals():
    yt = {"Chicago", "OnlyYouTube", "OnlyNational"}
    national = {"Chicago", "OnlyNational"}
    snapshot = {"Chicago"}
    assert bs.cross_source_artists(yt, national, snapshot) == {"Chicago"}


def test_match_events_uses_headliner_then_attractions():
    ev_head = {"event_id": "E1", "headliner": "Turnover", "attraction_names": "Turnover|Narrow Head"}
    ev_support = {"event_id": "E2", "headliner": "Some Opener", "attraction_names": "Some Opener|Chicago"}
    matched = bs.match_events_to_artists([ev_head, ev_support], {"Turnover", "Chicago"})
    assert matched == {"Turnover": [ev_head], "Chicago": [ev_support]}


def _ev(eid, comp=1.0, bay=False, date="2026-09-01"):
    return {"event_id": eid, "price_completeness": comp, "is_bay_area": bay, "show_date": date}


def test_rank_guarantees_a_bay_area_artist_when_one_exists():
    matched = {
        "A": [_ev("a1")], "B": [_ev("b1")], "C": [_ev("c1")],
        "Bay": [_ev("z1", bay=True)],
    }
    picks = bs.rank_seed_artists(matched, n=3)
    assert "Bay" in {a for a, _ in picks}
    assert len(picks) == 3


def test_pick_seed_event_prefers_bay_then_completeness():
    chosen = bs.pick_seed_event([
        _ev("nonbay", comp=1.0, bay=False),
        _ev("bay", comp=0.95, bay=True),
    ])
    assert chosen["event_id"] == "bay"


# ---------------------------------------------------------------------------
# Committed-fixture validation
# ---------------------------------------------------------------------------

def test_manifest_has_several_artists_and_a_bay_show(seed_manifest, seed_artists):
    assert len(seed_artists) >= 3
    assert len(seed_manifest["seed_events"]) == len(seed_artists)
    assert any(e["is_bay_area"] for e in seed_manifest["seed_events"]), "expected a Bay Area seed show"


def test_tm_events_are_the_complete_price_series(seed_tm_events, seed_event_ids, seed_manifest):
    assert len(seed_tm_events) == seed_manifest["counts"]["tm_events_rows"]
    cols = set(seed_tm_events[0])
    assert {"event_id", "snapshot_date", "price_min", "price_max"} <= cols
    assert {r["event_id"] for r in seed_tm_events} == seed_event_ids
    priced = sum(1 for r in seed_tm_events if (r.get("price_min") or "").strip())
    assert priced / len(seed_tm_events) >= COMPLETE_PRICE  # the "complete" property


def test_tm_raw_matches_bronze_shape(seed_tm_raw, seed_event_ids, seed_manifest):
    assert len(seed_tm_raw) == seed_manifest["counts"]["tm_raw_events"]
    assert {e["id"] for e in seed_tm_raw} <= seed_event_ids
    for ev in seed_tm_raw:
        assert {"id", "name", "_embedded"} <= set(ev), "raw TM event missing core keys"


def test_youtube_payloads_are_trimmed_to_seed_artists(seed_youtube_raw, seed_artists, seed_manifest):
    assert len(seed_youtube_raw) == seed_manifest["counts"]["youtube_files"]
    seen = set()
    for payload in seed_youtube_raw:
        assert payload["source"] == "youtube"
        for rec in payload["records"]:
            assert rec["query"] in seed_artists
            seen.add(rec["query"])
    assert seen == seed_artists  # every seed artist shows up on some day


def test_trends_payloads_cover_every_seed_artist(seed_trends_raw, seed_artists, seed_manifest):
    national = {p["artist"] for p in seed_trends_raw if p["resolution"] == "national"}
    snapshot = {p["artist"] for p in seed_trends_raw if p["resolution"] == "DMA"}
    for p in seed_trends_raw:
        assert p["source"] == "google_trends"
        assert p["n_records"] == len(p["records"])
    assert national == seed_artists, "every seed artist needs a national (iot) series"
    assert snapshot == seed_artists, "every seed artist needs a DMA snapshot (ibr)"
    counts = seed_manifest["counts"]
    n_national = sum(1 for p in seed_trends_raw if p["resolution"] == "national")
    n_snapshot = sum(1 for p in seed_trends_raw if p["resolution"] == "DMA")
    assert n_national == counts["trends_national_files"]
    assert n_snapshot == counts["trends_snapshot_files"]


def test_every_seed_artist_is_present_in_all_three_sources(
    seed_artists, seed_youtube_raw, seed_trends_raw, seed_tm_events, seed_manifest
):
    """The core invariant INT-1 / the gold join depend on: full cross-source coverage."""
    yt = {r["query"] for p in seed_youtube_raw for r in p["records"]}
    tr_national = {p["artist"] for p in seed_trends_raw if p["resolution"] == "national"}
    tr_snapshot = {p["artist"] for p in seed_trends_raw if p["resolution"] == "DMA"}
    assert seed_artists <= yt
    assert seed_artists <= tr_national
    assert seed_artists <= tr_snapshot
    # every seed event has Ticketmaster price rows
    tm_ids = {r["event_id"] for r in seed_tm_events}
    assert {e["event_id"] for e in seed_manifest["seed_events"]} == tm_ids
