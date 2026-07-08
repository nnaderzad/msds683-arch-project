"""G1 — gold-refresh orchestrator (the Cloud Run Job entrypoint).

Refreshes the **whole analytical state in one execution** so the three source
signals + gold + forecast never drift apart:

    A1  trends_to_silver.py         -> fact_trends        (silver)
    A1b trends_series_to_silver.py  -> fact_trends_daily  (silver, recent captures)
    A2  youtube_to_silver.py        -> fact_youtube       (silver)
    A3  build_dimensions.py         -> dim_*/bridge       (silver)
    A4  dbt build                -> fact_ticketmaster (silver) + fact_event_demand (gold)
    D2  export_predictions_table -> forecast_event_price (gold)
    C   GX forecast sanity gate  -> validate the fresh forecast (fail the run on violation)

Why this shape: the silver A1/A2/A3 transforms are still Python scripts (the dbt
migration MIG-1/2/3 isn't done), so this job runs them itself rather than waiting
on the migration. dbt is *called* (`dbt build`) — never edited — so the migration
stays a separate concern. The team plan anticipates exactly this: A1/A2 "fold into
G1 when automated" (team-plan.md). One job = one consistent snapshot per run.

Determinism / idempotency: every step is re-runnable (silver MERGE, dbt
incremental, forecast WRITE_TRUNCATE + fixed seed), so a retried execution
converges to the same state. Fail-fast: the first non-zero step aborts the run
(non-zero exit) so a partial refresh never silently ships.

Run:
    python pipeline/gold_refresh.py                 # full refresh + validate
    python pipeline/gold_refresh.py --dry-run       # build/report, write nothing
    python pipeline/gold_refresh.py --skip dbt_build # e.g. re-forecast only
    python pipeline/gold_refresh.py --only forecast_export validate_forecast
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"
DEFAULT_LOCATION = "us-west1"

FORECAST_TABLE = "forecast_event_price"

# fact_trends_daily incremental window: each iot capture carries the full ~269-day
# series, and the tier-1 rotation re-pulls every pair within ~4 days, so a 14-day
# capture window (>3 full cycles) refreshes every actively-collected series without
# re-reading the whole bronze prefix each run.
TRENDS_SERIES_LOOKBACK_DAYS = 14

# Scene sources (19hz / RA / ticketpages) land once per day and each capture is
# self-contained, so the loader only needs a short re-read window (idempotent
# MERGE makes overlap harmless; the margin covers a missed run or two).
SCENE_LOOKBACK_DAYS = 7

# fact_trends (ibr snapshot) window: each capture is a self-contained same-day
# cross-DMA snapshot that never changes after its day, so the nightly run only
# needs recent partitions (idempotent MERGE makes overlap harmless). Without a
# window this step re-downloaded the ENTIRE growing ibr bronze every night —
# 47 min on 2026-07-08 and rising, pushing the whole run toward the job's
# 3600s task timeout. Historical (re)loads: run trends_to_silver.py by hand
# with an explicit --start-date.
TRENDS_SNAPSHOT_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class Step:
    """One refresh step. `argv` is run from `cwd`; `validate` runs in-process instead."""

    name: str
    argv: list[str] = field(default_factory=list)
    cwd: Path = REPO_ROOT
    env: dict[str, str] = field(default_factory=dict)
    validate: bool = False  # GX gate — handled in-process, not as a subprocess


def build_steps(
    project: str,
    dataset: str,
    location: str = DEFAULT_LOCATION,
    *,
    dry_run: bool = False,
    today: date | None = None,
) -> list[Step]:
    """The ordered refresh plan. Pure given `today` — unit-testable offline."""
    py = sys.executable
    bq_flags = ["--project", project, "--dataset", dataset]
    run_flags = ["--dry-run"] if dry_run else []
    dbt_env = {
        "DBT_GCP_PROJECT": project,
        "DBT_BQ_DATASET": dataset,
        "DBT_BQ_LOCATION": location,
    }

    # A dry run materializes nothing, so dbt uses `compile` (parse + render SQL, no
    # warehouse writes) instead of `build`. The python steps take `--dry-run` directly.
    dbt_verb = "compile" if dry_run else "build"
    dbt_argv = ["dbt", dbt_verb, "--profiles-dir", ".", "--project-dir", "."]

    series_start = (today or date.today()) - timedelta(days=TRENDS_SERIES_LOOKBACK_DAYS)
    scene_start = (today or date.today()) - timedelta(days=SCENE_LOOKBACK_DAYS)
    snapshot_start = (today or date.today()) - timedelta(days=TRENDS_SNAPSHOT_LOOKBACK_DAYS)

    steps = [
        Step(
            "trends_silver",
            [
                py,
                "pipeline/silver/trends_to_silver.py",
                *bq_flags,
                "--start-date",
                snapshot_start.isoformat(),
                *run_flags,
            ],
        ),
        Step(
            "trends_series_silver",
            [
                py,
                "pipeline/silver/trends_series_to_silver.py",
                *bq_flags,
                "--start-date",
                series_start.isoformat(),
                *run_flags,
            ],
        ),
        Step("youtube_silver", [py, "pipeline/silver/youtube_to_silver.py", *bq_flags, *run_flags]),
        Step(
            "scene_silver",
            [
                py,
                "pipeline/silver/scene_to_silver.py",
                *bq_flags,
                "--start-date",
                scene_start.isoformat(),
                *run_flags,
            ],
        ),
        Step("dimensions", [py, "pipeline/silver/build_dimensions.py", *bq_flags, *run_flags]),
        Step("dbt_build", dbt_argv, cwd=DBT_DIR, env=dbt_env),
        Step(
            "forecast_export",
            [py, "pipeline/gold/export_predictions_table.py", *bq_flags, *run_flags],
        ),
    ]
    # The GX gate needs a materialized table to read; skip it on a dry run.
    if not dry_run:
        steps.append(Step("validate_forecast", validate=True))
    return steps


def _run_subprocess(step: Step) -> None:
    """Run a step's command, streaming output; raise on a non-zero exit (fail-fast)."""
    env = {**os.environ, **step.env}
    print(f"[gold_refresh] ▶ {step.name}: {' '.join(step.argv)} (cwd={step.cwd})", flush=True)
    result = subprocess.run(step.argv, cwd=str(step.cwd), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"step '{step.name}' failed (exit {result.returncode})")


def validate_forecast(project: str, dataset: str) -> None:
    """C/D2 gate — validate the freshly-written `forecast_event_price` against the GX
    forecast sanity suite (non-negative, finite, valid horizon, unique grain). Raises
    on any violation so the job exits non-zero and the bad refresh is caught."""
    sys.path.insert(0, str(REPO_ROOT / "great_expectations"))
    from google.cloud import bigquery

    import forecast_suites  # noqa: E402  (great_expectations/)

    target = f"{dataset}.{FORECAST_TABLE}"
    print(f"[gold_refresh] ▶ validate_forecast: GX sanity suite on {target}", flush=True)
    client = bigquery.Client(project=project)
    df = client.query(f"SELECT * FROM `{project}.{dataset}.{FORECAST_TABLE}`").to_dataframe()
    result = forecast_suites.run_forecast_suite(df)
    if not result.success:
        raise RuntimeError(f"GX forecast suite FAILED on {FORECAST_TABLE} ({len(df)} rows)")
    print(f"[gold_refresh] ✓ forecast suite PASSED ({len(df)} rows).", flush=True)


def run(
    *,
    project: str | None = None,
    dataset: str | None = None,
    location: str | None = None,
    dry_run: bool = False,
    only: list[str] | None = None,
    skip: list[str] | None = None,
) -> int:
    """Execute the refresh plan in order, fail-fast. Returns a process exit code."""
    project = project or os.environ.get("DBT_GCP_PROJECT", DEFAULT_PROJECT)
    dataset = dataset or os.environ.get("DBT_BQ_DATASET", DEFAULT_DATASET)
    location = location or os.environ.get("DBT_BQ_LOCATION", DEFAULT_LOCATION)

    steps = build_steps(project, dataset, location, dry_run=dry_run)
    if only:
        steps = [s for s in steps if s.name in set(only)]
    if skip:
        steps = [s for s in steps if s.name not in set(skip)]

    plan = ", ".join(s.name for s in steps) or "(none)"
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"[gold_refresh] {mode} refresh of {project}.{dataset} — steps: {plan}", flush=True)

    try:
        for step in steps:
            if step.validate:
                validate_forecast(project, dataset)
            else:
                _run_subprocess(step)
    except RuntimeError as exc:
        print(f"[gold_refresh] ✗ ABORTED: {exc}", file=sys.stderr, flush=True)
        return 1

    print(f"[gold_refresh] ✓ {mode} refresh complete.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh silver+gold+forecast in one run (G1).")
    parser.add_argument("--project", default=None, help="GCP project (default: $DBT_GCP_PROJECT).")
    parser.add_argument("--dataset", default=None, help="BQ dataset (default: $DBT_BQ_DATASET).")
    parser.add_argument("--location", default=None, help="BQ location (default: $DBT_BQ_LOCATION).")
    parser.add_argument("--dry-run", action="store_true", help="Build/report; write nothing.")
    parser.add_argument("--only", nargs="+", default=None, help="Run only these step names.")
    parser.add_argument("--skip", nargs="+", default=None, help="Skip these step names.")
    args = parser.parse_args(argv)
    return run(
        project=args.project,
        dataset=args.dataset,
        location=args.location,
        dry_run=args.dry_run,
        only=args.only,
        skip=args.skip,
    )


if __name__ == "__main__":
    raise SystemExit(main())
