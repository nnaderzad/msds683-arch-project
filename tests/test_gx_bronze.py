"""C2 — bronze GX suites pass on the seed and FAIL on corrupted fixtures.

Fully offline (no creds, no network): flattens the committed bronze seed fixtures
and validates them with the C2 suites built on the C1 scaffold. The corruption
tests are the done-when guarantee — a suite that can't fail isn't a check.
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
import bronze_suites  # noqa: E402  (import after the importorskip guard)


# --- passes on the seed -----------------------------------------------------

def test_all_bronze_suites_pass_on_seed(tmp_path):
    results = bronze_suites.run_bronze_offline(project_root=tmp_path)
    assert set(results) == set(bronze_suites.BRONZE_SOURCES)
    failed = {k: r for k, r in results.items() if not r.success}
    assert not failed, f"bronze suites failed on the seed: {list(failed)}"


@pytest.mark.parametrize("key", list(bronze_suites.BRONZE_SOURCES))
def test_each_extractor_is_non_empty(key):
    loader, _ = bronze_suites.BRONZE_SOURCES[key]
    df = loader()
    assert len(df) > 0, f"{key} extractor produced no rows from the seed"


# --- fails on corruption (the C2 done-when) ---------------------------------

def _run(tmp_path, key, df, suite):
    context = gx_project.get_context(project_root=tmp_path)
    return gx_project.run_suite_on_dataframe(context, df, suite, key=key)


def test_ticketmaster_fails_on_null_event_id(tmp_path):
    df = bronze_suites.load_ticketmaster_events()
    df.loc[0, "event_id"] = None
    res = _run(tmp_path, "corrupt_tm", df, bronze_suites.build_ticketmaster_bronze_suite())
    assert not res.success


def test_trends_dma_fails_on_out_of_range_interest(tmp_path):
    df = bronze_suites.load_trends_dma()
    df.loc[0, "interest"] = 200  # interest must be 0–100
    res = _run(tmp_path, "corrupt_trends_dma", df, bronze_suites.build_trends_dma_bronze_suite())
    assert not res.success


def test_trends_national_fails_on_null_date(tmp_path):
    df = bronze_suites.load_trends_national()
    df.loc[0, "date"] = None
    res = _run(tmp_path, "corrupt_trends_nat", df, bronze_suites.build_trends_national_bronze_suite())
    assert not res.success


def test_youtube_fails_on_negative_views(tmp_path):
    df = bronze_suites.load_youtube_records()
    df.loc[0, "official_total_views"] = -1
    res = _run(tmp_path, "corrupt_youtube", df, bronze_suites.build_youtube_bronze_suite())
    assert not res.success
