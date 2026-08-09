# Event Demand Forecasting — Data Architecture → Data Lakehouse

End-to-end data lakehouse for predicting **concert ticket resale demand**,
combining ticket/event data, global artist popularity, and local per-metro search
interest. Thesis: a *locally* popular artist in a *small* venue tends to sell out
and push resale prices up; a locally-unknown artist in a big room tends to soften.

Built in MSDS 683 (Designing a Data Architecture) and continued in the Data
Lakehouse course, which adds a guardrailed **text-to-SQL agent**, a **synthetic
demand layer** (`event_demand_synth`), and **performance benchmarks** — roadmap
and task board: [`docs/lakehouse-plan.md`](docs/lakehouse-plan.md).

> **Architecture & design decisions:** [`docs/architecture.md`](docs/architecture.md) ·
> current status, freshness, and incidents: [`docs/REPO_STATE.md`](docs/REPO_STATE.md) ·
> schema: [`docs/data-model.md`](docs/data-model.md) ·
> pipeline walkthrough: [`docs/transformations_showcase.md`](docs/transformations_showcase.md)

## Data sources

| Source | Signal | Status |
|---|---|---|
| **Ticketmaster** (Discovery API) | upcoming events, venues, genres, status, **current** price ranges | deployed — nationwide, 2×/day (05:00 + 15:00 PT) → `tm_events` + honest `tm_observations` |
| **Google Trends** (pytrends) | **per-metro (DMA)** search interest, with real history | deployed — backfill + daily |
| **YouTube** (Data API) | **global** popularity (subscribers) + momentum (Topic views) | deployed — daily snapshots |
| **19hz.info** | Bay Area club/warehouse listings with face-value prices (~75% priced) + full lineups | deployed — daily → `fact_nineteenhz` |
| **Resident Advisor** (GraphQL) | Bay Area listings + per-event `attending` count (a buzz signal no other source has) | deployed — 1 request/day by written agreement → `fact_ra` |
| **Ticket pages** (JSON-LD) | per-offer price + availability (incl. SoldOut) from the 19hz ticket URLs | deployed — daily → `fact_ticketpages` |

Honest limits that shape the design: Ticketmaster gives no *historical* resale
prices (we snapshot forward); YouTube has no geography and no history (forward
snapshots only) — so **Google Trends carries the geographic + historical signal**,
and the Bay Area scene sources (19hz / RA / ticket pages) carry the club-show
prices, lineups, and sell-out signals Ticketmaster structurally lacks.
Data joins on `(artist, DMA, date)` (Trends ↔ Ticketmaster), `artist` (YouTube),
and `(venue, date)` (scene sources).

## Architecture (medallion on GCP, project `data-architecture-498123`)

```
APIs ──► BRONZE (raw, GCS)                  ──► SILVER (BigQuery + processed/) ──► GOLD (analytics)
         gs://…-raw/<source>/dt=YYYY-MM-DD/      tm_events (MERGE), …               model-ready star schema
```

- **Bronze:** untouched API JSON/HTML, `dt=`-partitioned, via `common/gcs_io.py`.
- **Silver:** typed, deduped per-source tables (`tm_events`, honest
  `tm_observations` price history, `fact_trends` + `fact_trends_daily`,
  `fact_youtube`, `fact_nineteenhz` / `fact_ra` / `fact_ticketpages`, conformed dims).
- **Gold:** dbt star `fact_event_demand` + precomputed `forecast_event_price`
  (anchor+drift model in `model/`), refreshed daily by the `gold-refresh` job;
  plus the demo-only `fact_event_demand_continuous` (interior price gaps
  forward-filled, every filled row flagged `price_is_filled`).
- **Serving:** FastAPI + React dashboard in one Cloud Run service (`api/` + `web/`),
  reading gold live — plus `POST /ask`, a guardrailed text-to-SQL agent
  (Gemini via Vertex AI), and `/search` warehouse browse.

Compute is **Cloud Run** (functions + jobs) on **Cloud Scheduler**; infra is
**Terraform**; secrets in **Secret Manager**; failures alert via
**Cloud Monitoring**; **CI** runs on every push (GitHub Actions: ruff + pytest,
terraform fmt/validate on both roots, web test + build).

## Repo layout

```
├── common/                   # shared helpers: gcs_io.py (bronze landing), keys.py (surrogate ids)
├── ticketmaster_api/         # Ticketmaster POC
├── seatgeek_api/             # SeatGeek POC (pricing gated behind partner access — not deployed)
├── cloud_functions/
│   └── ticketmaster_daily/   # deployed nationwide TM extractor (Cloud Run fn)
├── google_trends_api/        # Google Trends ingestion (roster, geo, jobs) — see its README
├── youtube_api/              # YouTube POC + collect_youtube.py (deployed collector)
├── nineteenhz_api/           # 19hz.info scene collector + ticket-page JSON-LD poller — see its README
├── ra_api/                   # Resident Advisor collector (1 req/day by written agreement)
├── pipeline/                 # silver loaders + gold-refresh Cloud Run job — see its README
├── dbt/                      # dbt transforms: fact_ticketmaster, gold star + continuous, tests
├── model/                    # anchor+drift price forecaster (features/train/predict)
├── synth/                    # synthetic demand layer → event_demand_synth (never in honest tables)
├── reference/                # curated reference data (researched venue capacities)
├── api/  +  web/             # FastAPI service + React dashboard (one Cloud Run service)
├── eda/                      # committed, deterministic diagnostics (see eda/output/)
├── great_expectations/       # data-quality suites: bronze/silver/gold (GX) — see its README
├── tests/                    # pytest (offline; network/GCP faked)
├── docs/                     # REPO_STATE, data-model, decision records — see docs/README.md
├── final_presentation/       # deck build + hero-show curation outputs
├── terraform/                # main root: buckets, BigQuery, TM pipeline, gold-refresh, monitoring
│   └── gtrends/              # isolated root (remote GCS state): Trends + YouTube + scene jobs
└── environment.yml           # conda env `music-demand`
```

## Component docs

- Google Trends: [`google_trends_api/README.md`](google_trends_api/README.md) ·
  deploy: [`google_trends_api/DEPLOY.md`](google_trends_api/DEPLOY.md)
- Ticketmaster: [`ticketmaster_api/README.md`](ticketmaster_api/README.md) ·
  [`cloud_functions/ticketmaster_daily/README.md`](cloud_functions/ticketmaster_daily/README.md)
- YouTube: [`youtube_api/README.md`](youtube_api/README.md)
- Scene sources: [`nineteenhz_api/README.md`](nineteenhz_api/README.md) ·
  [`ra_api/README.md`](ra_api/README.md)
- Pipeline + gold refresh: [`pipeline/README.md`](pipeline/README.md) ·
  dbt: [`dbt/README.md`](dbt/README.md)
- Data quality (Great Expectations, bronze/silver/gold): [`great_expectations/README.md`](great_expectations/README.md)

## Terraform layout (two roots)

The **main** root ([`terraform/`](terraform/README.md)) holds the buckets,
BigQuery dataset, the Ticketmaster pipeline, the gold-refresh job, and
monitoring (state is local, on the maintainer's machine). The
[**`terraform/gtrends/`**](terraform/gtrends/README.md) root holds the Google
Trends + YouTube + scene-listing Cloud Run jobs and uses a **remote GCS
backend** (`…-tfstate`), so any teammate can plan/apply it without sharing
local state or the Ticketmaster key.

```bash
# Trends + YouTube + scene infra:
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
Cloud Scheduler/Artifact Registry negligible; the text-to-SQL agent ≈
$0.003/question (Gemini Flash on Vertex). Comfortably within the education
billing credits (billing details in [`docs/REPO_STATE.md`](docs/REPO_STATE.md)).
