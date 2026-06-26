# dbt — silver + gold transforms (BigQuery)

The ELT transform engine for the warehouse. dbt models are SQL pushed down to BigQuery;
dbt owns the analytical table DDL, materialization, tests, and lineage. Terraform owns the
containers (dataset, buckets, IAM). See `team-plan.md` → "Transform engine = dbt".

## Models

| Model | Layer | Grain | Source |
|---|---|---|---|
| `fact_ticketmaster` | silver | event × snapshot_date | processed parquet snapshots (external table) |
| `fact_event_demand` | gold | event × snapshot_date | the silver facts + dims (task B1) |

`fact_ticketmaster` is the price **history** (the gold spine) — distinct from the
current-state `tm_events` table (which feeds the A3 dims).

## Setup (one-time)

```bash
conda activate music-demand
pip install -r dbt/requirements.txt          # dbt-bigquery
gcloud auth application-default login        # ADC — no keys committed
cd dbt
dbt deps                                      # pull dbt_external_tables, dbt_utils
```

Profile/target default to the live project (`data-architecture-498123`,
`event_demand_analytics`, `us-west1`); override with `DBT_GCP_PROJECT` / `DBT_BQ_DATASET`
/ `DBT_BQ_LOCATION` to point at a sandbox.

## Run

```bash
cd dbt
# 1. (re)create the external table over the GCS parquet snapshots
dbt run-operation stage_external_sources --profiles-dir .
# 2. build + test a model
dbt build --select fact_ticketmaster --profiles-dir .
# rebuild the whole history (vs. incremental append):
dbt build --select fact_ticketmaster --full-refresh --profiles-dir .
```

CI does not run dbt yet — that's task **G3** (needs a BigQuery sandbox + creds). Until
then, verify by hand against BigQuery (Ground Rule 2).
