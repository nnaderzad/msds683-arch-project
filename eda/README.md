# eda/ — deterministic, committed analysis & QC scripts

Repo convention: analysis, calibration, and QC live in **committed, re-runnable
scripts** — never ad-hoc queries — so every number cited in the docs can be
reproduced identically after more data lands. Reports/CSVs/plots go to
`eda/output/`. **No LLM at runtime**, with one sanctioned exception:
`eval_text_to_sql.py` calls the real text-to-SQL agent (the thing under test);
its scoring is deterministic and offline-tested.

## Run

```bash
conda activate music-demand
pip install -r eda/requirements.txt      # matplotlib etc. (local-only deps)
gcloud auth application-default login    # scripts read BQ/GCS via ADC / the authed bq CLI

python eda/collection_sizing.py --freshness
```

## Scripts

| Script | One line |
|---|---|
| `collection_sizing.py` | freshness / TM pricing / Trends-pair sizing behind the collection review + REPO_STATE tables |
| `data_review.py` | full data-review report: bronze inventory, per-API field inventory, coverage, 19hz/RA/TM overlap |
| `profile_schema.py` | warehouse profile that backed the midterm schema decision (grains, price coverage, join hit-rates) |
| `build_schema_context.py` | generates the committed `api/schema_context.md` the text-to-SQL agent prompts with (INFORMATION_SCHEMA + curated notes + dry-run-validated few-shots) |
| `eval_text_to_sql.py` | runs `text2sql_eval_set.yaml` through the REAL agent, scores by execution-result match → `output/text_to_sql_eval.md` |
| `benchmark_trends_window.py` | BENCH-1: the trends_silver 14-day window fix, before/after from execution logs + bronze footprint |
| `benchmark_partitioning.py` | BENCH-2: partitioned/clustered vs flat gold at 50x synth scale (dry-run bytes + latency; create → verify → cleanup) |
| `synth_review.py` | QC of the synthetic layer: real-vs-synth distributions + anecdote-regime probes → `output/synth_review.md` |
| `hero_candidates.py` | curates the demo hero shows; **generates `web/src/data/heroShows.ts`** + `final_presentation/hero-candidates.*` |
| `backfill_hero_trends.py` | fresh real per-(artist, geo) Trends pulls for the hero shows (single pull per series — the 0–100 scale is per-pull) |
| `diagnose_price_movement.py` | the "96% of shows never move price" measurement the anchor+drift model rests on |
| `diagnose_forecast_bias.py` | forecast-vs-actual bias, old pooled vs new anchor model — the sign-off gate for the rework |
| `diagnose_price_gaps.py` | classifies missing price days: coverage gaps (re-fetch fixes) vs source gaps (it can't) |
| `diagnose_headliner_gap.py` | why priced shows lack a headliner `artist_id` (a TM no-attractions source ceiling) |
| `tm_price_eda.py` | historical price-panel completeness from the daily parquet snapshots |
| `tm_bronze_price_eda.py` | raw `priceRanges` anatomy in bronze: primary vs resale fill (resale = 0.0%), onsale timing, lineups |
| `tm_price_probe.py` | live Discovery-API probes: detail-poll pricing, Commerce endpoint, sweep truncation |
| `_common.py` | shared helpers (bq CLI access, committed-CSV IO, provenance stamps) |

`text2sql_eval_set.yaml` is the committed eval question set (easy / join /
aggregate / trick tiers, incl. expected refusals). `data_review_2026-07.md` is
a kept point-in-time snapshot of the July review.

Where the numbers feed decisions: [`../docs/collection_efficiency_review.md`](../docs/collection_efficiency_review.md),
[`../docs/forecast_model_decision.md`](../docs/forecast_model_decision.md), and
the task board in [`../docs/lakehouse-plan.md`](../docs/lakehouse-plan.md).
