# Benchmark — partitioning + clustering at 50x scale

Generated 2026-08-08T20:36:39+00:00 by `eda/benchmark_partitioning.py --run` (REPS=3, cache disabled).
Twins: `event_demand_synth.fact_event_demand_50x` (PARTITION BY snapshot_date, CLUSTER BY event_id — the production layout) vs `fact_event_demand_50x_flat` (same rows, no layout).

| query | actual bytes: flat | actual: part+cluster | saved | latency: flat | latency: p+c |
|---|---|---|---|---|---|
| recent_window (14-day dashboard window (partition pruning)) | 515.5 MiB | 0.0 MiB | 100.0% | 0.39 s | 0.39 s |
| single_day (one capture day (partition pruning, point date)) | 417.9 MiB | 16.2 MiB | 96.1% | 0.41 s | 0.39 s |
| event_history (one event's full history (clustering)) | 1,501.1 MiB | 310.0 MiB | 79.3% | 0.55 s | 0.45 s |
| agent_style_join_filter (agent-shaped filter: priced rows in a window) | 1,501.1 MiB | 867.0 MiB | 42.2% | 0.67 s | 0.75 s |
| full_scan_control (whole-table aggregate (control — no pruning possible)) | 417.9 MiB | 417.9 MiB | 0.0% | 0.39 s | 0.39 s |

Method: ACTUAL `total_bytes_processed` from uncached executed jobs (this is what BigQuery bills, and the only number that sees CLUSTER block-pruning; dry-run estimates — also in the CSV — only see partition pruning) + median wall-clock of 3 uncached runs per side. The full-scan control is expected to show ~no difference — pruning can only help queries that filter on the partition/cluster keys.
