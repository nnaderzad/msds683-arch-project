"""Offline tests for the windowing benchmark's pure log-parsing logic."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "eda"))

bm = importlib.import_module("benchmark_trends_window")


def entry(ts: str, payload: str, execution: str = "gold-refresh-abc") -> dict:
    return {
        "timestamp": ts,
        "textPayload": payload,
        "labels": {"run.googleapis.com/execution_name": execution},
    }


def test_step_durations_and_timeout_outcome():
    entries = [
        entry("2026-07-13T23:30:00Z", "[gold_refresh] ▶ trends_silver: python ..."),
        entry("2026-07-14T00:30:00Z", "Terminating task because it has reached the "
                                      "maximum timeout of 3600 seconds."),
    ]
    rows = bm.timelines_from_entries(entries)
    assert len(rows) == 1
    row = rows[0]
    assert row["step"] == "trends_silver"
    assert row["duration_s"] == "3600"
    assert row["step_outcome"] == "timeout"
    assert row["run_outcome"] == "timeout"


def test_multi_step_run_completed():
    entries = [
        entry("2026-08-08T23:30:00Z", "[gold_refresh] ▶ trends_silver: ..."),
        entry("2026-08-08T23:33:00Z", "[gold_refresh] ▶ youtube_silver: ..."),
        entry("2026-08-08T23:34:30Z", "[gold_refresh] ▶ dbt_build: ..."),
    ]
    rows = bm.timelines_from_entries(entries)
    assert [r["step"] for r in rows] == ["trends_silver", "youtube_silver", "dbt_build"]
    assert rows[0]["duration_s"] == "180"
    assert rows[1]["duration_s"] == "90"
    assert all(r["run_outcome"] == "completed" for r in rows)


def test_dbt_failure_marks_failed_run():
    entries = [
        entry("2026-07-09T23:30:00Z", "[gold_refresh] ▶ trends_silver: ...", "e1"),
        entry("2026-07-10T00:22:00Z", "[gold_refresh] ▶ dbt_build: ...", "e1"),
        entry("2026-07-10T00:24:00Z", "Task gold-refresh-e1-task0 failed with exit code: 1", "e1"),
    ]
    rows = bm.timelines_from_entries(entries)
    assert rows[0]["duration_s"] == str(52 * 60)
    assert rows[-1]["step"] == "dbt_build"
    assert rows[-1]["step_outcome"] == "failed"
    assert rows[-1]["run_outcome"] == "failed"


def test_merge_runs_csv_idempotent(tmp_path):
    path = tmp_path / "runs.csv"
    rows = [{
        "execution": "e1", "started_utc": "2026-07-13T23:30:00+00:00",
        "step": "trends_silver", "duration_s": "3600",
        "step_outcome": "timeout", "run_outcome": "timeout",
    }]
    assert bm.merge_runs_csv(rows, path) == 1
    assert bm.merge_runs_csv(rows, path) == 0  # same (execution, step) → no dupes
    content = path.read_text(encoding="utf-8")
    assert content.count("e1") == 1


def test_report_renders_before_and_after_sections():
    runs = [
        {"execution": "old", "started_utc": "2026-07-13T23:30:00+00:00",
         "step": "trends_silver", "duration_s": "3600",
         "step_outcome": "timeout", "run_outcome": "timeout"},
        {"execution": "new", "started_utc": "2026-08-08T23:30:00+00:00",
         "step": "trends_silver", "duration_s": "150",
         "step_outcome": "ok", "run_outcome": "completed"},
    ]
    footprint = [
        {"scope": "all_time (pre-fix nightly read)", "objects": "5049", "mib": "102.7"},
        {"scope": "trailing 14d (post-fix nightly read)", "objects": "1275", "mib": "25.9"},
    ]
    text = bm.render_report(runs, footprint, "2026-08-08T00:00:00+00:00")
    assert "60.0 min ← timeout" in text
    assert "2.5 min" in text
    assert "5049" in text and "1275" in text
    assert "Before the fix" in text and "After the fix" in text
