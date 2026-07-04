# Event Demand Forecasting — Data Architecture (MSDS 683)

End-to-end data architecture for predicting **concert ticket resale demand**,
combining ticket/event data, global artist popularity, and local per-metro search
interest. Thesis: a *locally* popular artist in a *small* venue tends to sell out
and push resale prices up; a locally-unknown artist in a big room tends to soften.

> **Current status, freshness, and incidents:** [`docs/REPO_STATE.md`](docs/REPO_STATE.md) ·
> schema: [`docs/data-model.md`](docs/data-model.md) ·
> pipeline walkthrough: [`docs/transformations_showcase.md`](docs/transformations_showcase.md)

## Data sources

| Source | Signal | Status |
|---|---|---|
| **Ticketmaster** (Discovery API) | upcoming events, venues, genres, status, **current** price ranges | deployed — nationwide, every 4h → `tm_events` |
| **Google Trends** (pytrends) | **per-metro (DMA)** search interest, with real history | deployed — backfill + daily |
| **YouTube** (Data API) | **global** popularity (subscribers) + momentum (Topic views) | deployed — daily snapshots |

Honest limits that shape the design: Ticketmaster gives no *historical* resale
prices (we snapshot forward); YouTube has no geography and no history (forward
snapshots only) — so **Google Trends carries the geographic + historical signal**.
Data joins on `(artist, DMA, date)` (Trends ↔ Ticketmaster) and `artist` (YouTube).

## Architecture (medallion on GCP, project `data-architecture-498123`)

```
APIs ──► BRONZE (raw, GCS)                  ──► SILVER (BigQuery + processed/) ──► GOLD (analytics)
         gs://…-raw/<source>/dt=YYYY-MM-DD/      tm_events (MERGE), …               model-ready star schema
```

- **Bronze:** untouched API JSON, `dt=`-partitioned, via `common/gcs_io.py`.
- **Silver:** typed, deduped per-source tables (`tm_events`, honest
  `tm_observations` price history, `fact_trends*`, `fact_youtube`, conformed dims).
- **Gold:** dbt star `fact_event_demand` + precomputed `forecast_event_price`
  (anchor+drift model in `model/`), refreshed daily by the `gold-refresh` job.
- **Serving:** FastAPI + React dashboard in one Cloud Run service (`api/` + `web/`),
  reading gold live.

Compute is **Cloud Run** (functions + jobs) on **Cloud Scheduler**; infra is
**Terraform** (the +20 bonus); secrets in **Secret Manager**; failures alert via
**Cloud Monitoring**; **CI** runs on every push (GitHub Actions: ruff + pytest +
terraform validate).

## Repo layout

```
├── common/                   # shared helpers: gcs_io.py (bronze landing), keys.py (surrogate ids)
├── ticketmaster_api/         # Ticketmaster POC
├── cloud_functions/
│   └── ticketmaster_daily/   # deployed nationwide TM extractor (Cloud Run fn)
├── google_trends_api/        # Google Trends ingestion (roster, geo, jobs) — see its README
├── youtube_api/              # YouTube POC + collect_youtube.py (deployed collector)
├── pipeline/                 # silver/gold Python builders + gold-refresh Cloud Run job
├── dbt/                      # dbt transforms: fact_ticketmaster, gold star, data tests
├── model/                    # anchor+drift price forecaster (features/train/predict)
├── api/  +  web/             # FastAPI service + React dashboard (one Cloud Run service)
├── eda/                      # committed, deterministic diagnostics (see eda/output/)
├── great_expectations/       # data-quality suites: bronze/silver/gold (GX) — see its README
├── tests/                    # pytest (offline; network/GCP faked)
├── docs/                     # REPO_STATE, data-model, decision records
├── terraform/                # main root: buckets, BigQuery, TM pipeline, monitoring
│   └── gtrends/              # isolated root (remote GCS state): Trends + YouTube jobs
└── environment.yml           # conda env `music-demand`
```

## Component docs

- Google Trends: [`google_trends_api/README.md`](google_trends_api/README.md) ·
  deploy: [`google_trends_api/DEPLOY.md`](google_trends_api/DEPLOY.md)
- Ticketmaster: [`ticketmaster_api/README.md`](ticketmaster_api/README.md) ·
  [`cloud_functions/ticketmaster_daily/README.md`](cloud_functions/ticketmaster_daily/README.md)
- YouTube: [`youtube_api/README.md`](youtube_api/README.md)
- Data quality (Great Expectations, bronze/silver/gold): [`great_expectations/README.md`](great_expectations/README.md)

## Terraform layout (two roots)

The **main** root (`terraform/`) holds the buckets, BigQuery dataset, the
Ticketmaster pipeline, and monitoring (state is local, on the maintainer's
machine). The **`terraform/gtrends/`** root holds the Google Trends + YouTube
Cloud Run jobs and uses a **remote GCS backend** (`…-tfstate`), so any teammate
can plan/apply it without sharing local state or the Ticketmaster key.

```bash
# Google Trends + YouTube infra:
terraform -chdir=terraform/gtrends init
terraform -chdir=terraform/gtrends apply     # see google_trends_api/DEPLOY.md
```

## Prerequisites

```bash
brew install --cask google-cloud-sdk
# Terraform: HashiCorp tap, or a direct binary to ~/.local/bin
gcloud auth login && gcloud auth application-default login
gcloud config set project data-architecture-498123
```

## Estimated cost

Demo-scale: GCS standard storage ≈ pennies/month; BigQuery within the free tier;
Cloud Run jobs bill per vCPU-second (an hours-long backfill ≈ cents–low dollars);
Cloud Scheduler/Artifact Registry negligible. Comfortably within the $300 GCP
new-account credit. (The Trends/YouTube backfills are the only notable compute,
and they're bounded + idempotent.)
