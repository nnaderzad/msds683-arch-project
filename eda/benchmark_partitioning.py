#!/usr/bin/env python3
"""Benchmark: BigQuery partitioning + clustering, measured at 50x synthetic scale.

Secondary performance benchmark for the lakehouse class. The real gold star is
small enough (tens of MiB) that partition pruning looks modest, so the benchmark
runs on a 50x replicated copy (~55 M rows, a few GiB — SYNTH-4) living in the
`event_demand_synth` dataset:

  * ``fact_event_demand_50x``        — PARTITION BY snapshot_date, CLUSTER BY event_id
                                       (the production layout)
  * ``fact_event_demand_50x_flat``   — identical rows, no partitioning/clustering

A fixed query suite (the shapes our dashboards and the text-to-SQL agent
actually produce) runs against both twins: **dry-run bytes** (deterministic) and
median wall-clock latency over N repetitions. Lifecycle is explicit
(create → verify → benchmark → cleanup) and the copies never touch the honest
dataset.

Run (repo root, ADC authed):

    python eda/benchmark_partitioning.py --setup      # build both twins (CTAS)
    python eda/benchmark_partitioning.py --run        # measure + write report
    python eda/benchmark_partitioning.py --cleanup    # drop the twins

Outputs: ``eda/output/benchmark_partitioning.md`` + ``.csv``.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
from _common import DEFAULT_PROJECT, utc_now_iso  # noqa: E402

REAL_DATASET = "event_demand_analytics"
SYNTH_DATASET = "event_demand_synth"
SCALE = 50
PART_TABLE = f"fact_event_demand_{SCALE}x"
FLAT_TABLE = f"{PART_TABLE}_flat"
REPS = 3

OUT_MD = REPO_ROOT / "eda" / "output" / "benchmark_partitioning.md"
OUT_CSV = REPO_ROOT / "eda" / "output" / "benchmark_partitioning.csv"

SETUP_PARTITIONED = """
CREATE OR REPLACE TABLE `{p}.{s}.{part}`
PARTITION BY snapshot_date
CLUSTER BY event_id
AS
SELECT
  CONCAT(f.event_id, '_', CAST(r AS STRING)) AS event_id,
  f.snapshot_date,
  f.* EXCEPT (event_id, snapshot_date),
  r AS synth_replica
FROM `{p}.{r}.fact_event_demand` f
CROSS JOIN UNNEST(GENERATE_ARRAY(1, {scale})) AS r
"""

SETUP_FLAT = """
CREATE OR REPLACE TABLE `{p}.{s}.{flat}` AS
SELECT * FROM `{p}.{s}.{part}`
"""

# (query_id, description, sql template — {table} substituted per twin)
QUERY_SUITE: list[tuple[str, str, str]] = [
    (
        "recent_window",
        "14-day dashboard window (partition pruning)",
        "SELECT COUNT(*) n, ROUND(AVG(price_min), 2) avg_min FROM {table} "
        "WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)",
    ),
    (
        "single_day",
        "one capture day (partition pruning, point date)",
        "SELECT COUNT(*) n FROM {table} WHERE snapshot_date = '2026-07-01'",
    ),
    (
        "event_history",
        "one event's full history (clustering)",
        "SELECT snapshot_date, price_min FROM {table} "
        "WHERE event_id = 'rZ7HnEZ1Af00jd_1' ORDER BY snapshot_date",
    ),
    (
        "agent_style_join_filter",
        "agent-shaped filter: priced rows in a window",
        "SELECT COUNT(DISTINCT event_id) n FROM {table} "
        "WHERE snapshot_date BETWEEN '2026-06-15' AND '2026-06-30' "
        "AND price_min IS NOT NULL",
    ),
    (
        "full_scan_control",
        "whole-table aggregate (control — no pruning possible)",
        "SELECT COUNT(*) n, ROUND(AVG(days_to_show), 1) d FROM {table}",
    ),
]


def _client():
    from google.cloud import bigquery

    return bigquery.Client(project=DEFAULT_PROJECT), bigquery


def setup(project: str) -> None:
    client, _bq = _client()
    print(f"[bench] building {PART_TABLE} ({SCALE}x CTAS)…")
    client.query(SETUP_PARTITIONED.format(
        p=project, s=SYNTH_DATASET, r=REAL_DATASET, part=PART_TABLE, scale=SCALE
    )).result()
    print(f"[bench] building {FLAT_TABLE} (unpartitioned twin)…")
    client.query(SETUP_FLAT.format(p=project, s=SYNTH_DATASET, part=PART_TABLE,
                                   flat=FLAT_TABLE)).result()
    for table in (PART_TABLE, FLAT_TABLE):
        t = client.get_table(f"{project}.{SYNTH_DATASET}.{table}")
        print(f"[bench] {table}: {t.num_rows:,} rows, {t.num_bytes / 1024**3:.2f} GiB")


def measure(project: str) -> list[dict]:
    client, bigquery = _client()
    rows: list[dict] = []
    for query_id, description, template in QUERY_SUITE:
        for label, table in (("partitioned", PART_TABLE), ("flat", FLAT_TABLE)):
            sql = template.format(table=f"`{project}.{SYNTH_DATASET}.{table}`")
            dry = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True))
            latencies, actual_bytes = [], 0
            for _ in range(REPS):
                config = bigquery.QueryJobConfig(use_query_cache=False)
                start = time.monotonic()
                job = client.query(sql, job_config=config)
                job.result()
                latencies.append(time.monotonic() - start)
                # Actual bytes see CLUSTER block-pruning; dry-run only sees
                # partition pruning (it reports the pre-execution upper bound).
                actual_bytes = int(job.total_bytes_processed or 0)
            rows.append({
                "query_id": query_id,
                "description": description,
                "layout": label,
                "dry_run_bytes": int(dry.total_bytes_processed or 0),
                "actual_bytes": actual_bytes,
                "median_latency_s": round(statistics.median(latencies), 2),
            })
            print(f"[bench] {query_id:24s} {label:12s} "
                  f"dry {rows[-1]['dry_run_bytes'] / 1024**2:9.1f} MiB  "
                  f"actual {rows[-1]['actual_bytes'] / 1024**2:9.1f} MiB  "
                  f"{rows[-1]['median_latency_s']:5.2f} s")
    return rows


def write_report(rows: list[dict], as_of: str) -> None:
    import csv as csv_mod

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    by_query: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_query.setdefault(row["query_id"], {})[row["layout"]] = row

    lines = [
        "# Benchmark — partitioning + clustering at 50x scale",
        "",
        f"Generated {as_of} by `eda/benchmark_partitioning.py --run` "
        f"(REPS={REPS}, cache disabled).",
        f"Twins: `{SYNTH_DATASET}.{PART_TABLE}` (PARTITION BY snapshot_date, CLUSTER BY "
        f"event_id — the production layout) vs `{FLAT_TABLE}` (same rows, no layout).",
        "",
        "| query | actual bytes: flat | actual: part+cluster | saved | latency: flat | latency: p+c |",
        "|---|---|---|---|---|---|",
    ]
    for query_id, layouts in by_query.items():
        flat, part = layouts.get("flat", {}), layouts.get("partitioned", {})
        fb, pb = flat.get("actual_bytes", 0), part.get("actual_bytes", 0)
        saved = f"{(1 - pb / fb) * 100:.1f}%" if fb else "—"
        lines.append(
            f"| {query_id} ({flat.get('description', '')}) "
            f"| {fb / 1024**2:,.1f} MiB | {pb / 1024**2:,.1f} MiB | {saved} "
            f"| {flat.get('median_latency_s', '—')} s | {part.get('median_latency_s', '—')} s |"
        )
    lines += [
        "",
        "Method: ACTUAL `total_bytes_processed` from uncached executed jobs (this is what "
        "BigQuery bills, and the only number that sees CLUSTER block-pruning; dry-run "
        "estimates — also in the CSV — only see partition pruning) + median wall-clock of "
        f"{REPS} uncached runs per side. The full-scan control is expected to show ~no "
        "difference — pruning can only help queries that filter on the partition/cluster keys.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_mod.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[bench] wrote {OUT_MD}")


def cleanup(project: str) -> None:
    client, _bq = _client()
    for table in (FLAT_TABLE, PART_TABLE):
        client.query(f"DROP TABLE IF EXISTS `{project}.{SYNTH_DATASET}.{table}`").result()
        print(f"[bench] dropped {table}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    if args.setup:
        setup(args.project)
    if args.run:
        write_report(measure(args.project), utc_now_iso())
    if args.cleanup:
        cleanup(args.project)
    if not (args.setup or args.run or args.cleanup):
        parser.error("pass --setup and/or --run and/or --cleanup")


if __name__ == "__main__":
    main()
