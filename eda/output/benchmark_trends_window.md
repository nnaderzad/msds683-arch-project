# Benchmark — `trends_silver` full-bronze read vs 14-day window

Generated 2026-08-08T20:13:13+00:00 by `eda/benchmark_trends_window.py --report`.
Optimization under test: PR #55 windows the gold-refresh `trends_silver` step to
the trailing 14 days of ibr bronze instead of re-reading the entire
prefix nightly. Deployed 2026-08-08 (image git-ab65e00) after the un-windowed
step timed out the whole nightly job every night from 2026-07-12.

## Step runtime (from Cloud Run execution logs, banked before 30-day expiry)

### Before the fix (image git-82cafa2 and older)

| started (UTC) | execution | trends_silver runtime | run outcome |
|---|---|---|---|
| 2026-07-09T23:30 | gold-refresh-vgnfv | 51.7 min | completed |
| 2026-07-10T23:30 | gold-refresh-6mcpf | 59.0 min | timeout |
| 2026-07-11T23:30 | gold-refresh-ps2q8 | 54.8 min | completed |
| 2026-07-12T23:30 | gold-refresh-ntp8m | 60.0 min ← timeout | timeout |
| 2026-07-13T23:30 | gold-refresh-2n9sj | 60.0 min ← timeout | timeout |
| 2026-07-14T23:30 | gold-refresh-68skp | 60.0 min ← timeout | timeout |
| 2026-07-15T23:30 | gold-refresh-fbcr4 | 60.0 min ← timeout | timeout |
| 2026-07-16T23:30 | gold-refresh-79ddq | 60.0 min ← timeout | timeout |
| 2026-07-17T23:30 | gold-refresh-jqgnx | 60.0 min ← timeout | timeout |
| 2026-07-18T23:30 | gold-refresh-567qr | 60.0 min ← timeout | timeout |
| 2026-07-19T23:30 | gold-refresh-fbwqf | 59.9 min ← timeout | timeout |
| 2026-07-20T23:30 | gold-refresh-g5dtg | 60.0 min ← timeout | timeout |
| 2026-07-21T23:30 | gold-refresh-gxbkw | 60.0 min ← timeout | timeout |
| 2026-07-22T23:30 | gold-refresh-8x8gz | 60.0 min ← timeout | timeout |
| 2026-07-23T23:30 | gold-refresh-xgl2v | 60.0 min ← timeout | timeout |
| 2026-07-24T23:30 | gold-refresh-gqn2d | 60.0 min ← timeout | timeout |
| 2026-07-25T23:30 | gold-refresh-nrnxp | 60.0 min ← timeout | timeout |
| 2026-07-26T23:30 | gold-refresh-fcj88 | 60.0 min ← timeout | timeout |
| 2026-07-27T23:30 | gold-refresh-bgmhq | 60.0 min ← timeout | timeout |
| 2026-07-28T23:30 | gold-refresh-ngw8t | 60.0 min ← timeout | timeout |
| 2026-07-29T23:30 | gold-refresh-sfj8w | 60.0 min ← timeout | timeout |
| 2026-07-30T23:30 | gold-refresh-snpmc | 60.0 min ← timeout | timeout |
| 2026-07-31T23:30 | gold-refresh-vgvlc | 60.0 min ← timeout | timeout |
| 2026-08-01T23:30 | gold-refresh-gmr76 | 60.0 min ← timeout | timeout |
| 2026-08-02T23:30 | gold-refresh-6gjbp | 60.0 min ← timeout | timeout |
| 2026-08-03T23:30 | gold-refresh-777wf | 60.0 min ← timeout | timeout |
| 2026-08-04T23:30 | gold-refresh-z4ltz | 60.0 min ← timeout | timeout |
| 2026-08-05T23:30 | gold-refresh-gfbdr | 60.0 min ← timeout | timeout |
| 2026-08-06T23:30 | gold-refresh-l22k6 | 60.0 min ← timeout | timeout |
| 2026-08-07T23:30 | gold-refresh-dpmnt | 59.9 min ← timeout | timeout |

Note: `timeout` rows are censored at the 3600 s task ceiling — the true
runtime is *at least* the shown value; the job was killed mid-step.

### After the fix (image git-ab65e00, windowed)

| started (UTC) | execution | trends_silver runtime | run outcome |
|---|---|---|---|
| _no post-fix scheduled runs captured yet — re-run --capture-logs after 16:30 PT_ ||||

## Data volume the step reads (GCS listing, deterministic)

| scope | objects | MiB |
|---|---|---|
| all_time (pre-fix nightly read) | 5049 | 102.7 |
| trailing 14d (post-fix nightly read) | 1275 | 25.9 |

## Context

- Documented incident: docs/REPO_STATE.md incident log (2026-07-05 → 08-08).
- The same windowing family: `TRENDS_SERIES_LOOKBACK_DAYS = 14`,
  `SCENE_LOOKBACK_DAYS = 7` in pipeline/gold_refresh.py.
- Raw rows: benchmark_trends_window_runs.csv / _footprint.csv (same dir).
