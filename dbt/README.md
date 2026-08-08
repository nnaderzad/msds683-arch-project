# dbt — silver + gold transforms (BigQuery)

The ELT engine for the warehouse's SQL layer. dbt models are SQL pushed down to
BigQuery; dbt owns the analytical table DDL, materialization, tests, and
lineage. Terraform owns the containers (dataset, buckets, IAM); bronze→silver
JSON/HTML parsing stays in Python (`pipeline/silver/`) until the MIG-1/2/3
migration. Schema: [`../docs/data-model.md`](../docs/data-model.md).

## Models (three)

| Model | Layer | Materialization | Grain / role |
|---|---|---|---|
| `fact_ticketmaster` | silver | incremental (MERGE) | event × snapshot_date price history, read from the HONEST `silver.tm_observations` (observed days only, **no forward-fill**) — the gold spine |
| `fact_event_demand` | gold | incremental (MERGE) | the star: TM spine kept whole + headliner Trends/YouTube LEFT-joins; partitioned by `snapshot_date`, clustered by `event_id` |
| `fact_event_demand_continuous` | gold | table | **DEMO ONLY, team-derived**: interior price gaps forward-filled, every filled row flagged `price_is_filled` — never train on it or report coverage from it |

The `ticketmaster_raw.tm_snapshots_ext` external-table source is **vestigial**
(documented as such in `models/sources.yml`): it exported current-state
`tm_events` and carried last-known prices forward, so `tm_observations`
replaced it as the price-fact source. Kept for reference/replay only — nothing
selects from it.

## Tests

- PK `unique`/`not_null` on every model, plus the singular
  `tests/assert_gold_rows_eq_spine.sql` (the no-row-drop invariant).
- FK `relationships` tests run at **warn severity on purpose**: the fact is
  point-in-time history while dims rebuild from current `tm_events`, so small
  historical drift is expected — at ERROR this blocked every gold refresh
  2026-07-05..08 (see the REPO_STATE incident log).
- The `price_is_filled` `accepted_values` test carries `quote: false` on its
  BOOL values — dbt's default quoting renders `'True'`/`'False'` strings, which
  BigQuery rejects with a Database Error that aborts the build *regardless of
  severity* (broke the 2026-07-08 gold run). Don't remove it.

## Setup (one-time)

```bash
conda activate music-demand
pip install -r dbt/requirements.txt          # dbt-bigquery
gcloud auth application-default login        # ADC — no keys committed
cd dbt
dbt deps                                     # dbt_utils, dbt_external_tables
```

Profile/target default to the live project (`data-architecture-498123`,
`event_demand_analytics`, `us-west1`); override with `DBT_GCP_PROJECT` /
`DBT_BQ_DATASET` / `DBT_BQ_LOCATION` to point at a sandbox.

## Run

```bash
cd dbt
dbt build --profiles-dir .                              # all three models + tests
dbt build --select fact_event_demand --profiles-dir .   # one model
dbt build --full-refresh --profiles-dir .               # rebuild history from scratch
```

In production the nightly `gold-refresh` job runs `dbt build` as one step —
see [`../pipeline/GOLD_REFRESH.md`](../pipeline/GOLD_REFRESH.md). CI still does
not run dbt (task **G3**, needs a BigQuery sandbox + creds); verify changes by
hand against BigQuery.
