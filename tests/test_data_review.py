"""Offline unit tests for the pure helpers in eda/data_review.py.

No network, no GCS, no BigQuery — mirrors the repo's offline-by-default test
discipline. The bronze/BQ sections are exercised by running the script itself.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "eda"))
sys.path.insert(0, str(ROOT_DIR))

import data_review  # noqa: E402


# --- truncate -----------------------------------------------------------------

def test_truncate_caps_lists_and_marks_remainder():
    out = data_review.truncate({"records": list(range(10))}, max_list=3)
    assert out["records"][:3] == [0, 1, 2]
    assert out["records"][3] == "... 7 more items"


def test_truncate_caps_long_strings_and_preserves_structure():
    obj = {"a": "x" * 500, "b": {"c": [{"d": "y" * 500}]}, "n": 7}
    out = data_review.truncate(obj, max_str=100)
    assert len(out["a"]) == 101 and out["a"].endswith("…")
    assert len(out["b"]["c"][0]["d"]) == 101
    assert out["n"] == 7  # non-strings untouched
    assert list(out.keys()) == ["a", "b", "n"]  # key order preserved


def test_truncate_is_pure():
    obj = {"records": [1, 2, 3, 4, 5]}
    assert data_review.truncate(obj) == data_review.truncate(obj)
    assert obj["records"] == [1, 2, 3, 4, 5]  # input not mutated


# --- norm_venue ---------------------------------------------------------------

def test_norm_venue_matches_across_source_spellings():
    # TM vs 19hz spellings of the same venues must collide.
    assert data_review.norm_venue("The Great Northern") == data_review.norm_venue(
        "Great Northern")
    assert data_review.norm_venue("Public Works (Loft)") == data_review.norm_venue(
        "Public Works")
    assert data_review.norm_venue("1015 Folsom") == data_review.norm_venue(
        "1015  FOLSOM ")
    assert data_review.norm_venue("Cafe du Nord") == data_review.norm_venue(
        "Cafe-du-Nord")


def test_norm_venue_keeps_distinct_venues_distinct():
    assert data_review.norm_venue("The Independent") != data_review.norm_venue(
        "Independence Hall")
    assert data_review.norm_venue(None) == ""


# --- overlap_sets / shared_window ----------------------------------------------

def test_shared_window_is_range_intersection():
    assert data_review.shared_window(
        ("2026-07-06", "2027-02-25"),   # 19hz
        ("2026-07-08", "2026-07-24"),   # RA (one page)
        ("2026-07-08", "2027-01-03"),   # TM
    ) == ("2026-07-08", "2026-07-24")


def test_overlap_sets_classifies_presence_within_window():
    hz = {("venue a", "2026-07-10"), ("venue b", "2026-07-10"),
          ("venue x", "2026-08-01")}  # outside window
    ra = {("venue a", "2026-07-10"), ("venue c", "2026-07-11")}
    tm = {("venue a", "2026-07-10"), ("venue b", "2026-07-10"),
          ("venue d", "2026-07-12")}
    counts = data_review.overlap_sets(hz, ra, tm, ("2026-07-08", "2026-07-24"))
    assert counts["19hz_in_window"] == 2  # venue x excluded by window
    assert counts["all_three"] == 1       # venue a
    assert counts["hz_and_tm"] == 2       # venue a + venue b
    assert counts["only_ra"] == 1         # venue c
    assert counts["only_tm"] == 1         # venue d
    assert counts["only_19hz"] == 0
    assert counts["union"] == 4


# --- md_table -------------------------------------------------------------------

def test_md_table_renders_github_markdown():
    lines = data_review.md_table([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    assert lines[0] == "| a | b |"
    assert lines[1] == "|---|---|"
    assert lines[2] == "| 1 | x |"
    assert data_review.md_table([]) == ["*(no rows)*"]


def test_pct_handles_zero_denominator():
    assert data_review.pct(1, 4) == "25.0%"
    assert data_review.pct(0, 0) == "—"
