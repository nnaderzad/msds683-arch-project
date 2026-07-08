"""Offline unit tests for the G1 gold-refresh orchestrator.

Exercise the *pure* plan builder (`build_steps`) and the run-time step selection
(`--only` / `--skip` / `--dry-run`) without executing any command or touching the
network — mirrors the repo's offline-by-default test discipline.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "pipeline"))

import gold_refresh  # noqa: E402


def _names(steps):
    return [s.name for s in steps]


def _record_steps(monkeypatch, seen):
    """Stub out the two side-effecting runners so run() only records step order."""
    monkeypatch.setattr(gold_refresh, "_run_subprocess", lambda step: seen.append(step.name))
    monkeypatch.setattr(
        gold_refresh, "validate_forecast", lambda p, d: seen.append("validate_forecast")
    )


def test_full_plan_is_in_dependency_order():
    steps = gold_refresh.build_steps("proj", "ds")
    assert _names(steps) == [
        "trends_silver",
        "trends_series_silver",
        "youtube_silver",
        "scene_silver",
        "dimensions",
        "dbt_build",
        "forecast_export",
        "validate_forecast",
    ]


def test_trends_series_step_loads_a_recent_capture_window():
    from datetime import date

    steps = {
        s.name: s
        for s in gold_refresh.build_steps("proj", "ds", today=date(2026, 7, 20))
    }
    argv = steps["trends_series_silver"].argv
    assert argv[1].endswith("trends_series_to_silver.py")
    # 14-day lookback keeps the daily refresh incremental (>3 tier-1 rotation
    # cycles, so every actively-collected series is covered).
    assert argv[argv.index("--start-date") + 1] == "2026-07-06"


def test_scene_step_loads_a_recent_capture_window():
    from datetime import date

    steps = {
        s.name: s
        for s in gold_refresh.build_steps("proj", "ds", today=date(2026, 7, 20))
    }
    argv = steps["scene_silver"].argv
    assert argv[1].endswith("scene_to_silver.py")
    # Scene captures are self-contained daily files; 7 days covers missed runs.
    assert argv[argv.index("--start-date") + 1] == "2026-07-13"


def test_trends_snapshot_step_loads_a_recent_capture_window():
    from datetime import date

    steps = {
        s.name: s
        for s in gold_refresh.build_steps("proj", "ds", today=date(2026, 7, 20))
    }
    argv = steps["trends_silver"].argv
    assert argv[1].endswith("trends_to_silver.py")
    # ibr snapshots are self-contained per day; the unwindowed step re-downloaded
    # the whole growing bronze prefix every night (47 min on 2026-07-08, vs the
    # job's 3600s task timeout).
    assert argv[argv.index("--start-date") + 1] == "2026-07-06"


def test_silver_and_forecast_steps_carry_project_dataset():
    steps = {s.name: s for s in gold_refresh.build_steps("proj", "ds")}
    for name in (
        "trends_silver",
        "trends_series_silver",
        "youtube_silver",
        "scene_silver",
        "dimensions",
        "forecast_export",
    ):
        argv = steps[name].argv
        assert argv[1].endswith(".py")
        assert "--project" in argv and argv[argv.index("--project") + 1] == "proj"
        assert "--dataset" in argv and argv[argv.index("--dataset") + 1] == "ds"
        assert "--dry-run" not in argv  # live run by default


def test_dbt_step_runs_from_dbt_dir_with_env_overrides():
    dbt = {s.name: s for s in gold_refresh.build_steps("proj", "ds", "us-west1")}["dbt_build"]
    assert dbt.argv[:2] == ["dbt", "build"]
    assert dbt.cwd == gold_refresh.DBT_DIR
    assert dbt.env == {
        "DBT_GCP_PROJECT": "proj",
        "DBT_BQ_DATASET": "ds",
        "DBT_BQ_LOCATION": "us-west1",
    }


def test_dry_run_propagates_and_drops_the_gx_gate():
    steps = gold_refresh.build_steps("proj", "ds", dry_run=True)
    # The validate gate needs a materialized table, so it is skipped on a dry run.
    assert "validate_forecast" not in _names(steps)
    # Every python step gets --dry-run; dbt uses `compile` (no warehouse writes).
    assert all("--dry-run" in s.argv for s in steps if s.name != "dbt_build")
    dbt = {s.name: s for s in steps}["dbt_build"]
    assert dbt.argv[:2] == ["dbt", "compile"]


def test_only_selects_a_subset(monkeypatch):
    seen = []
    _record_steps(monkeypatch, seen)

    rc = gold_refresh.run(project="p", dataset="d", only=["forecast_export", "validate_forecast"])
    assert rc == 0
    assert seen == ["forecast_export", "validate_forecast"]


def test_skip_drops_steps(monkeypatch):
    seen = []
    _record_steps(monkeypatch, seen)

    gold_refresh.run(project="p", dataset="d", skip=["dbt_build"])
    assert "dbt_build" not in seen
    assert seen[0] == "trends_silver" and seen[-1] == "validate_forecast"


def test_failfast_aborts_on_first_failure(monkeypatch):
    def boom(step):
        if step.name == "youtube_silver":
            raise RuntimeError("step 'youtube_silver' failed (exit 1)")

    seen = []
    _record_steps(monkeypatch, seen)
    monkeypatch.setattr(gold_refresh, "_run_subprocess",
                        lambda step: seen.append(step.name) or boom(step))

    rc = gold_refresh.run(project="p", dataset="d")
    assert rc == 1
    # Stopped at the failing step — nothing after it ran.
    assert seen == ["trends_silver", "trends_series_silver", "youtube_silver"]
