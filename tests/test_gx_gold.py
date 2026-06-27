"""C4 — gold GX suite: fact_event_demand integrity, offline over seed-built gold.

Replicates the dbt gold join in pandas from the C3 seed-built silver, then checks the
star's integrity: no-row-drop vs the spine, unique grain, non-negative price, in-range
joined signals, and (per-event) days_to_show monotonic toward the show date. Negative
tests prove the suite bites.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "great_expectations"))

gx_project = pytest.importorskip(
    "gx_project",
    reason="great_expectations not installed (pip install -r great_expectations/requirements.txt)",
)
import gold_suites  # noqa: E402  (after the importorskip guard)
import silver_suites  # noqa: E402


def test_gold_suite_passes_on_seed(tmp_path):
    results = gold_suites.run_gold_offline(project_root=tmp_path)
    assert set(results) == set(gold_suites.GOLD_TABLES)
    assert results["fact_event_demand"].success


def test_no_row_drop_vs_spine():
    """Every spine row survives the LEFT JOINs; none dropped or fanned out."""
    gold, spine_rows = gold_suites.build_gold_frame()
    assert len(gold) == spine_rows
    assert not gold.duplicated(["event_id", "snapshot_date"]).any()


def test_days_to_show_monotone_toward_show_date():
    """Per event, days_to_show strictly decreases as snapshot_date advances."""
    gold, _ = gold_suites.build_gold_frame()
    for event_id, grp in gold.groupby("event_id"):
        ordered = grp.sort_values("snapshot_date")
        diffs = ordered["days_to_show"].diff().dropna()
        assert (diffs < 0).all(), f"days_to_show not monotonic for event {event_id}"


# --- negative tests -----------------------------------------------------------

def _run_on(tmp_path, gold, frames, spine_rows):
    context = gx_project.get_context(project_root=tmp_path)
    suite = gold_suites.build_gold_suite(frames, spine_rows)
    return gx_project.run_suite_on_dataframe(context, gold, suite, key="corrupt_gold")


def test_fails_on_row_drop(tmp_path):
    frames = silver_suites.build_silver_frames()
    gold, spine_rows = gold_suites.build_gold_frame(frames)
    res = _run_on(tmp_path, gold.iloc[:-1].copy(), frames, spine_rows)  # one row short of the spine
    assert not res.success


def test_fails_on_out_of_range_local_interest(tmp_path):
    frames = silver_suites.build_silver_frames()
    gold, spine_rows = gold_suites.build_gold_frame(frames)
    gold.loc[gold.index[0], "local_interest"] = 200
    res = _run_on(tmp_path, gold, frames, spine_rows)
    assert not res.success
