"""C3 — silver GX suites pass on seed-built tables and catch violations.

Offline: rebuilds the silver layer from the committed seed with the A1/A2/A3
transforms and validates schema / nulls / value ranges / join-key integrity.
The negative tests prove the suites actually bite.
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
import silver_suites  # noqa: E402  (after the importorskip guard)


def test_all_silver_suites_pass_on_seed(tmp_path):
    results = silver_suites.run_silver_offline(project_root=tmp_path)
    assert set(results) == set(silver_suites.SILVER_TABLES)
    failed = {t: r for t, r in results.items() if not r.success}
    assert not failed, f"silver suites failed on the seed: {list(failed)}"


def _corrupt_and_run(tmp_path, table, mutate):
    context = gx_project.get_context(project_root=tmp_path)
    frames = silver_suites.build_silver_frames()
    suites = silver_suites.build_suites(frames)
    df = frames[table].copy()
    mutate(df)
    return gx_project.run_suite_on_dataframe(context, df, suites[table], key=f"corrupt_{table}")


def test_fact_trends_fails_on_out_of_range_interest(tmp_path):
    res = _corrupt_and_run(tmp_path, "fact_trends", lambda df: df.__setitem__("interest", 200))
    assert not res.success


def test_dim_artist_fails_on_duplicate_pk(tmp_path):
    def dup(df):
        df.loc[df.index[-1], "artist_id"] = df.loc[df.index[0], "artist_id"]
    res = _corrupt_and_run(tmp_path, "dim_artist", dup)
    assert not res.success


def test_bridge_fails_on_orphan_artist_fk(tmp_path):
    res = _corrupt_and_run(tmp_path, "bridge_event_artist",
                           lambda df: df.__setitem__("artist_id", -999))
    assert not res.success


def test_fact_ticketmaster_fails_on_negative_price(tmp_path):
    res = _corrupt_and_run(tmp_path, "fact_ticketmaster",
                           lambda df: df.__setitem__("price_min", -5))
    assert not res.success
