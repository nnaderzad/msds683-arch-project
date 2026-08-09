# Benchmark — partitioning + clustering at 50x scale

Generated 2026-08-09T08:18:28+00:00 by `eda/benchmark_partitioning.py --run` (REPS=3, cache disabled).
Twins: `event_demand_synth.fact_event_demand_50x` (PARTITION BY snapshot_date, CLUSTER BY event_id — the production layout) vs `fact_event_demand_50x_flat` (same rows, no layout).

| query | actual bytes: flat | actual: part+cluster | saved | latency: flat | latency: p+c |
|---|---|---|---|---|---|
| recent_window (14-day dashboard window (partition pruning)) | 987.1 MiB | 233.6 MiB | 76.3% | 0.42 s | 0.45 s |
| single_day (one capture day (partition pruning, point date)) | 803.2 MiB | 16.2 MiB | 98.0% | 0.39 s | 0.39 s |
| event_history (one event's full history (clustering)) | 2,877.1 MiB | 629.8 MiB | 78.1% | 0.46 s | 0.45 s |
| agent_style_join_filter (agent-shaped filter: priced rows in a window) | 2,877.1 MiB | 867.0 MiB | 69.9% | 0.58 s | 0.71 s |
| full_scan_control (whole-table aggregate (control — no pruning possible)) | 803.2 MiB | 803.2 MiB | 0.0% | 0.37 s | 0.42 s |

Method: ACTUAL `total_bytes_processed` from uncached executed jobs (this is what BigQuery bills, and the only number that sees CLUSTER block-pruning; dry-run estimates — also in the CSV — only see partition pruning) + median wall-clock of 3 uncached runs per side. The full-scan control is expected to show ~no difference — pruning can only help queries that filter on the partition/cluster keys.
