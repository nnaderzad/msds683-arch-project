# G1 — Gold-refresh Cloud Run Job

One scheduled job that refreshes the **whole analytical state in a single execution**, so
the three source signals, the gold star, and the price forecast never drift apart.

```
Cloud Scheduler (daily, America/Los_Angeles)
        │  POST …/jobs/gold-refresh:run   (OAuth, invoke-only SA)
        ▼
Cloud Run Job  ──>  python pipeline/gold_refresh.py
        1. trends_to_silver.py         fact_trends        (silver, A1 — 14-day window)
        2. trends_series_to_silver.py  fact_trends_daily  (silver — 14-day window)
        3. scene_to_silver.py          fact_nineteenhz/_ra/_ticketpages (7-day window)
        4. youtube_to_silver.py        fact_youtube       (silver, A2)
        5. build_dimensions.py         dim_* / bridge     (silver, A3 + capacity fill)
        6. dbt build                   fact_ticketmaster (silver) + fact_event_demand (gold)
        7. export_predictions_table    forecast_event_price (gold, D2)
        8. GX forecast sanity gate     validate the fresh forecast — fail the run on violation
```

The read windows exist because the un-windowed `trends_silver` step re-read the
whole growing ibr bronze nightly and timed the job out for a month
(2026-07/08 incident — see `eda/output/benchmark_trends_window.md`).

Fail-fast: the first failing step aborts the run with a non-zero exit, so a partial refresh
never silently ships. Every step is **idempotent** (silver `MERGE`, dbt incremental, forecast
`WRITE_TRUNCATE` + fixed seed), so a retried execution converges to the same state.

## Why it runs the silver scripts itself

The A1/A2/A3 silver transforms are still Python (the dbt migration `MIG-1/2/3` isn't done).
Rather than wait on that migration, the job runs them directly so **all three datasets land in
one consistent snapshot per run**. `dbt build` is *called*, never edited. When `MIG-1/2/3`
lands, replace steps 1–3 with a single `dbt build` that also covers them — nothing else changes.

## Files

| File | Role |
|---|---|
| `pipeline/gold_refresh.py` | Orchestrator + entrypoint. `build_steps()` is pure (unit-tested offline). |
| `pipeline/gold_refresh.requirements.txt` | Image deps (BQ + pandas/sklearn + dbt-bigquery + GX). |
| `pipeline/gold_refresh.Dockerfile` | Job image; ADC auth (no keys baked in). |
| `pipeline/gold_refresh.cloudbuild.yaml` | Cloud Build spec (custom Dockerfile path). |
| `terraform/gold_refresh_job.tf` | Artifact Registry repo, image build, Cloud Run Job, SA + IAM, Scheduler. |
| `tests/test_gold_refresh.py` | Offline tests: plan order, flags, `--only`/`--skip`, dry-run, fail-fast. |

## Deploy

```bash
cd terraform
terraform apply        # builds + pushes the image (Cloud Build), provisions job + scheduler
```

Auth is **ADC** end-to-end: the job's service account (`gold-refresh-job`) supplies credentials
at runtime; `dbt/profiles.yml` uses `method: oauth` → ADC. No service-account keys are baked
into the image.

## Run on demand / backfill

```bash
# Full refresh now (outside the schedule):
gcloud run jobs execute gold-refresh --region us-west1

# Local dry-run (assemble + report, write nothing, skip the GX gate):
python pipeline/gold_refresh.py --dry-run

# Re-forecast only (gold already fresh):
python pipeline/gold_refresh.py --only forecast_export validate_forecast

# Skip a step (e.g. dbt unavailable locally):
python pipeline/gold_refresh.py --skip dbt_build
```

Project / dataset / location resolve from `--project/--dataset/--location` or the
`DBT_GCP_PROJECT` / `DBT_BQ_DATASET` / `DBT_BQ_LOCATION` env vars (same defaults as the rest of
the repo), which the Cloud Run Job sets from Terraform.

## Schedule

`var.gold_refresh_schedule` — deployed as `30 16 * * *` America/Los_Angeles
(16:30 PT, the D8 cadence: after the 15:00 PT TM sweep + YouTube run, so gold
builds from same-day captures; see REPO_STATE §Clock & cadence). Once a day is
plenty for a precomputed-forecast product.
