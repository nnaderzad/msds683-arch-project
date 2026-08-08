#!/usr/bin/env python3
"""Benchmark: the `trends_silver` 14-day read window (PR #55) — before vs after.

The performance benchmark for the lakehouse class, measured on a REAL production
failure: the nightly gold-refresh job's `trends_silver` step re-read the ENTIRE
growing ibr bronze prefix every night. Measured 47 min on 2026-07-08, it grew to
52 → 59 min and from 2026-07-12 it exceeded the job's 3600 s task timeout — every
nightly run died before dbt for a month. PR #55 windows the step to the last 14
days of bronze; deployed 2026-08-08 as image git-ab65e00.

Two deterministic measurements:

  * ``--capture-logs``: pull the gold-refresh execution logs (Cloud Logging via
    gcloud, explicit window) and reduce them to per-execution step timelines →
    ``eda/output/benchmark_trends_window_runs.csv``. Application logs expire
    after 30 days, so the pre-fix evidence is banked in the committed CSV; later
    captures append the post-fix runs (the CSV is keyed by execution name and
    re-capture is idempotent).
  * ``--gcs-footprint``: list the ibr bronze prefix and total objects/bytes
    ALL-TIME vs the trailing 14-day window — the data-volume side of the same
    before/after (what the step used to read vs reads now).

``--report`` renders both into ``eda/output/benchmark_trends_window.md``.
Re-run the same commands to refresh; results only grow as new runs land.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
from _common import DEFAULT_PROJECT, utc_now_iso  # noqa: E402

OUT_DIR = REPO_ROOT / "eda" / "output"
RUNS_CSV = OUT_DIR / "benchmark_trends_window_runs.csv"
FOOTPRINT_CSV = OUT_DIR / "benchmark_trends_window_footprint.csv"
REPORT_MD = OUT_DIR / "benchmark_trends_window.md"

JOB_NAME = "gold-refresh"
STEP_RE = re.compile(r"\[gold_refresh\] ▶ (\w+):")
TIMEOUT_RE = re.compile(r"maximum timeout of (\d+) seconds")
TASK_FAIL_RE = re.compile(r"failed with exit code")
BRONZE_PATTERN = "gs://{project}-raw/google_trends/dt=*/google_trends_ibr_DMA_*.json"
WINDOW_DAYS = 14

CSV_FIELDS = ["execution", "started_utc", "step", "duration_s", "step_outcome", "run_outcome"]


# ---------------------------------------------------------------------------
# Log capture → step timelines (pure parsing is unit-testable)
# ---------------------------------------------------------------------------


def fetch_log_entries(project: str, freshness_days: int) -> list[dict]:
    """Read gold-refresh job log entries via the authenticated gcloud CLI."""
    log_filter = (
        f'resource.type="cloud_run_job" AND resource.labels.job_name="{JOB_NAME}" AND '
        '(textPayload:"[gold_refresh]" OR textPayload:"Terminating task" OR '
        'textPayload:"failed with exit code")'
    )
    proc = subprocess.run(
        ["gcloud", "logging", "read", log_filter, f"--project={project}",
         f"--freshness={freshness_days}d", "--limit=5000", "--format=json",
         "--order=asc"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout or "[]")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def timelines_from_entries(entries: list[dict]) -> list[dict[str, str]]:
    """Reduce raw log entries to one row per (execution, step) with durations.

    A step's duration runs from its ▶ line to the next ▶ line or to the terminal
    event (timeout / task-failure / last log line). The run outcome is `timeout`,
    `failed`, or `completed` (all steps seen and no terminal error).
    """
    by_exec: dict[str, list[tuple[datetime, str]]] = {}
    for entry in entries:
        execution = (
            entry.get("labels", {}).get("run.googleapis.com/execution_name")
            or entry.get("resource", {}).get("labels", {}).get("execution_name")
            or "unknown"
        )
        payload = entry.get("textPayload", "")
        if not payload:
            continue
        by_exec.setdefault(execution, []).append((_parse_ts(entry["timestamp"]), payload))

    rows: list[dict[str, str]] = []
    for execution, events in sorted(by_exec.items()):
        events.sort(key=lambda pair: pair[0])
        run_outcome = "completed"
        terminal_ts = events[-1][0]
        for ts, payload in events:
            if TIMEOUT_RE.search(payload):
                run_outcome, terminal_ts = "timeout", ts
            elif TASK_FAIL_RE.search(payload):
                if run_outcome != "timeout":
                    run_outcome, terminal_ts = "failed", ts

        steps: list[tuple[str, datetime]] = [
            (match.group(1), ts)
            for ts, payload in events
            if (match := STEP_RE.search(payload))
        ]
        if not steps:
            continue
        started = steps[0][1]
        for index, (step, step_start) in enumerate(steps):
            step_end = steps[index + 1][1] if index + 1 < len(steps) else terminal_ts
            is_last = index + 1 == len(steps)
            step_outcome = run_outcome if is_last and run_outcome != "completed" else "ok"
            rows.append({
                "execution": execution,
                "started_utc": started.isoformat(),
                "step": step,
                "duration_s": str(max(0, int((step_end - step_start).total_seconds()))),
                "step_outcome": step_outcome,
                "run_outcome": run_outcome,
            })
    return rows


def merge_runs_csv(new_rows: list[dict[str, str]], path: Path) -> int:
    """Idempotent append: existing (execution, step) rows win; new ones append."""
    existing: dict[tuple[str, str], dict[str, str]] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                existing[(row["execution"], row["step"])] = row
    added = 0
    for row in new_rows:
        key = (row["execution"], row["step"])
        if key not in existing:
            existing[key] = row
            added += 1
    ordered = sorted(existing.values(), key=lambda r: (r["started_utc"], r["execution"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(ordered)
    return added


# ---------------------------------------------------------------------------
# GCS footprint: what the step reads, all-time vs windowed
# ---------------------------------------------------------------------------


def gcs_footprint(project: str, window_days: int = WINDOW_DAYS) -> list[dict[str, str]]:
    pattern = BRONZE_PATTERN.format(project=project)
    proc = subprocess.run(["gsutil", "ls", "-l", pattern],
                          capture_output=True, text=True, check=True)
    cutoff = (date.today() - timedelta(days=window_days)).isoformat()
    total_objects = total_bytes = win_objects = win_bytes = 0
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[2].startswith("gs://"):
            continue
        size, uri = int(parts[0]), parts[2]
        dt_match = re.search(r"/dt=(\d{4}-\d{2}-\d{2})/", uri)
        total_objects += 1
        total_bytes += size
        if dt_match and dt_match.group(1) >= cutoff:
            win_objects += 1
            win_bytes += size
    return [
        {"scope": "all_time (pre-fix nightly read)", "objects": str(total_objects),
         "mib": f"{total_bytes / 1024**2:.1f}"},
        {"scope": f"trailing {window_days}d (post-fix nightly read)",
         "objects": str(win_objects), "mib": f"{win_bytes / 1024**2:.1f}"},
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render_report(runs: list[dict[str, str]], footprint: list[dict[str, str]],
                  as_of: str) -> str:
    trends = [r for r in runs if r["step"] == "trends_silver"]
    pre = [r for r in trends if r["started_utc"] < "2026-08-08T20:00:00"]
    post = [r for r in trends if r["started_utc"] >= "2026-08-08T20:00:00"]

    def fmt(rows: list[dict[str, str]]) -> list[str]:
        out = []
        for row in sorted(rows, key=lambda r: r["started_utc"]):
            minutes = int(row["duration_s"]) / 60
            note = "" if row["step_outcome"] == "ok" else f" ← {row['step_outcome']}"
            out.append(
                f"| {row['started_utc'][:16]} | {row['execution']} | {minutes:.1f} min"
                f"{note} | {row['run_outcome']} |"
            )
        return out

    lines = [
        "# Benchmark — `trends_silver` full-bronze read vs 14-day window",
        "",
        f"Generated {as_of} by `eda/benchmark_trends_window.py --report`.",
        "Optimization under test: PR #55 windows the gold-refresh `trends_silver` step to",
        f"the trailing {WINDOW_DAYS} days of ibr bronze instead of re-reading the entire",
        "prefix nightly. Deployed 2026-08-08 (image git-ab65e00) after the un-windowed",
        "step timed out the whole nightly job every night from 2026-07-12.",
        "",
        "## Step runtime (from Cloud Run execution logs, banked before 30-day expiry)",
        "",
        "### Before the fix (image git-82cafa2 and older)",
        "",
        "| started (UTC) | execution | trends_silver runtime | run outcome |",
        "|---|---|---|---|",
        *fmt(pre),
        "",
        "Note: `timeout` rows are censored at the 3600 s task ceiling — the true",
        "runtime is *at least* the shown value; the job was killed mid-step.",
        "",
        "### After the fix (image git-ab65e00, windowed)",
        "",
        "| started (UTC) | execution | trends_silver runtime | run outcome |",
        "|---|---|---|---|",
        *(fmt(post) or ["| _no post-fix scheduled runs captured yet — re-run --capture-logs after 16:30 PT_ ||||"]),
        "",
        "## Data volume the step reads (GCS listing, deterministic)",
        "",
        "| scope | objects | MiB |",
        "|---|---|---|",
        *[f"| {r['scope']} | {r['objects']} | {r['mib']} |" for r in footprint],
        "",
        "## Context",
        "",
        "- Documented incident: docs/REPO_STATE.md incident log (2026-07-05 → 08-08).",
        "- The same windowing family: `TRENDS_SERIES_LOOKBACK_DAYS = 14`,",
        "  `SCENE_LOOKBACK_DAYS = 7` in pipeline/gold_refresh.py.",
        "- Raw rows: benchmark_trends_window_runs.csv / _footprint.csv (same dir).",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--capture-logs", action="store_true")
    parser.add_argument("--freshness-days", type=int, default=30)
    parser.add_argument("--gcs-footprint", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    if args.capture_logs:
        rows = timelines_from_entries(fetch_log_entries(args.project, args.freshness_days))
        added = merge_runs_csv(rows, RUNS_CSV)
        print(f"[benchmark] captured {len(rows)} step rows ({added} new) -> {RUNS_CSV}")

    if args.gcs_footprint:
        rows = gcs_footprint(args.project)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with FOOTPRINT_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["scope", "objects", "mib"])
            writer.writeheader()
            writer.writerows(rows)
        for row in rows:
            print(f"[benchmark] {row['scope']}: {row['objects']} objects, {row['mib']} MiB")

    if args.report:
        runs = list(csv.DictReader(RUNS_CSV.open(encoding="utf-8"))) if RUNS_CSV.exists() else []
        footprint = (
            list(csv.DictReader(FOOTPRINT_CSV.open(encoding="utf-8")))
            if FOOTPRINT_CSV.exists() else []
        )
        REPORT_MD.write_text(render_report(runs, footprint, utc_now_iso()) + "\n",
                             encoding="utf-8")
        print(f"[benchmark] wrote {REPORT_MD}")

    if not (args.capture_logs or args.gcs_footprint or args.report):
        parser.error("nothing to do: pass --capture-logs and/or --gcs-footprint and/or --report")


if __name__ == "__main__":
    main()
