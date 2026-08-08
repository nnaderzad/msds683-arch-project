# pipeline/ — silver loaders + the gold-refresh orchestrator

Python bronze→silver transforms plus the one job that refreshes the whole
analytical state (silver → dbt → forecast → validation) in a single execution.
Where every table sits in the schema: [`../docs/data-model.md`](../docs/data-model.md);
live freshness/status: [`../docs/REPO_STATE.md`](../docs/REPO_STATE.md).

## Silver loaders (`silver/`)

| Script | Builds | One line |
|---|---|---|
| `tm_observations_to_silver.py` | `tm_observations` | the HONEST TM price history from raw bronze — one row per (event, day actually observed), no forward-fill; the cloud function appends incrementally, this script rebuilds/backfills |
| `trends_to_silver.py` | `fact_trends` | Google Trends `interest_by_region` DMA snapshots → (artist, dma, snapshot_date) |
| `trends_series_to_silver.py` | `fact_trends_daily` | per-DMA `interest_over_time` daily trajectories — additive to `fact_trends` (different 0–100 normalizations; never mix the columns) |
| `youtube_to_silver.py` | `fact_youtube` | daily channel/topic stats per (artist, snapshot_date) |
| `scene_to_silver.py` | `fact_nineteenhz` / `fact_ra` / `fact_ticketpages` | 19hz + RA + ticket-page JSON-LD bronze, parsed with the collectors' own committed parsers so the two can't drift |
| `build_dimensions.py` | `dim_*` + `bridge_event_artist` | conformed dims from `tm_events` + committed crosswalks; fills `dim_venue.capacity` from the curated [`../reference/venue_capacities.csv`](../reference/README.md); fact-referenced dims accumulate (MERGE, never delete) |

Every loader follows the same pattern: parse bronze → load a **staging table**
→ **MERGE** on a deterministic surrogate key from `common/keys.py`, so
re-loading any window is idempotent. Shared flags: `--dry-run` (parse + count,
write nothing), `--start-date`/`--end-date` (bound which bronze `dt=`
partitions are read — what keeps the nightly job fast), `--from-fixtures`
(offline test input), `--project`/`--dataset`.

## Run locally

```bash
conda activate music-demand
pip install -r pipeline/requirements.txt
gcloud auth application-default login        # ADC — no keys

python pipeline/silver/tm_observations_to_silver.py --dry-run
python pipeline/silver/trends_to_silver.py --start-date 2026-08-01
python pipeline/silver/build_dimensions.py
```

## Gold refresh (`gold_refresh.py`)

The Cloud Run Job entrypoint. One fail-fast execution runs, in order:
`trends_silver` → `trends_series_silver` → `youtube_silver` → `scene_silver` →
`dimensions` → `dbt_build` (silver `fact_ticketmaster` + the gold star) →
`forecast_export` (`gold/export_predictions_table.py` → `forecast_event_price`)
→ `validate_forecast` (GX sanity gate). Recent-partition windows (14 d Trends,
7 d scene) stop the steps from re-reading the whole growing bronze prefix every
night — the failure mode that killed the job for a month in July.

```bash
python pipeline/gold_refresh.py --dry-run
python pipeline/gold_refresh.py --only forecast_export validate_forecast
python pipeline/gold_refresh.py --skip dbt_build
```

Job image, deploy, schedule, and on-demand execution:
[`GOLD_REFRESH.md`](GOLD_REFRESH.md).
