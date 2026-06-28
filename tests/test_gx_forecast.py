"""D2 — GX forecast sanity suite passes on a valid forecast and fails on a bad one."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "great_expectations"))

gx_project = pytest.importorskip(
    "gx_project",
    reason="great_expectations not installed (pip install -r great_expectations/requirements.txt)",
)
import forecast_suites  # noqa: E402


def _forecast_df():
    return pd.DataFrame({
        "event_id": ["e1", "e1", "e2"],
        "days_to_show": [1, 0, 0],
        "predicted_price": [55.0, 57.0, 88.0],
    })


def test_forecast_suite_passes(tmp_path):
    res = forecast_suites.run_forecast_suite(_forecast_df(), project_root=tmp_path)
    assert res.success


def test_forecast_suite_fails_on_negative_price(tmp_path):
    df = _forecast_df()
    df.loc[0, "predicted_price"] = -1
    res = forecast_suites.run_forecast_suite(df, project_root=tmp_path)
    assert not res.success
