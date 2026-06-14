# Project strategy — Event-demand forecasting data architecture

*MSDS 683 (Designing a Data Architecture) group project. Living document — last
updated 2026-06-13. Some sections describe teammates' pieces from the outside and
are marked **[confirm with team]**.*

## TL;DR

We're building a data architecture that predicts **concert ticket resale demand** by
combining, per upcoming show: the **event + ticket price** (Ticketmaster), the
artist's **global popularity/momentum** (YouTube), and — critically — the artist's
**local, per-metro search interest** (Google Trends). The thesis: *demand is high
when a locally-popular artist plays a relatively small venue, and soft when a
locally-unknown artist plays a large one.* The geographic dimension is what makes
"local popularity" possible, and **Google Trends is the only source that provides
it** (Spotify was dropped for lacking geo; YouTube's API has no geo either).

## Domain & business goal

**Goal:** help a buyer/seller/analyst anticipate whether a given concert will sell
out and which way resale prices will move, early enough to act.

**Analytical use cases** (rubric asks for 3–4):

1. **Sell-out / price-pressure score per upcoming event** — combine local artist
   popularity, venue capacity, and days-to-show into a demand index.
2. **Local vs. national popularity gap** — find artists who are disproportionately
   popular in a specific metro (a DMA where Trends interest >> their national
   average) → those shows are underpriced-risk / sell-out candidates.
3. **Price trajectory vs. interest trajectory** — as a show approaches, does rising
   search interest lead rising listed prices? (needs the forward-accumulating
   snapshots, see *Temporal richness*).
4. **Genre / market heatmap** — which genres and metros show the most demand
   pressure over a season (festival season, tours).

## The demand intuition (why these sources)

| Signal | Source | Role in the model |
|---|---|---|
| Which shows exist, where, when, at what listed price | **Ticketmaster** | The event spine + the (forward-only) price signal we're predicting |
| How big the artist is overall | **YouTube** | Global magnitude of popularity (subscribers = brand, Topic views = streaming momentum) |
| How big the artist is **in this metro** | **Google Trends** | The geographic distribution of interest — turns "popular" into "popular *here*" |
| Venue capacity | **[confirm with team]** (Ticketmaster venue + external capacity lookup) | Demand is relative to supply; small venue + high local interest = squeeze |

## Data sources — capabilities and hard limits

Honest constraints that shape the whole architecture:

- **Ticketmaster (Discovery API v2)** — current/upcoming events nationwide (all 50
  states + DC), venue, genre, status, sale windows, and *current* price ranges
  (~33% coverage). **No historical resale prices.** A price *time-series* only
  accrues from repeated snapshots (started 2026-06-08). 🚩 Real price history would
  need a purchased/third-party dataset — flagged as the project's biggest thesis
  risk.
- **YouTube (Data API v3)** — global `subscriberCount` / `viewCount` only.
  **No geographic breakdown** (needs owner-OAuth Analytics API) and **no history**
  (current snapshot only). Forward-snapshot-only, like TM prices. → start collecting
  now (see `docs/team_messages.md`).
- **Google Trends (pytrends)** — per-metro (Nielsen DMA) search interest,
  **with real backfill**: ~270 days of *daily* data per pull, or ~5 years *weekly*.
  This is the one source where we can reconstruct history *and* the only one with
  geography. **→ Google Trends is the geographic + historical backbone.** Caveats:
  values are relative 0–100 within each (artist, geo, window); rate-limited
  (unofficial endpoint) → we fetch efficiently and pace politely.

## Key risks & decisions

- **🚩 Price history is forward-only.** Frame Ticketmaster as event-discovery +
  forward price snapshots, not a historical resale feed. Lean on temporal
  accumulation (below). Optionally scope a third-party historical price source later.
- **Scale target is 100 GB–TBs; we're at ~5 GB.** The rubric allows *either* handling
  that volume *or* defending decisions that work at that scale. Our plan: (a)
  **temporal richness** — all three sources snapshot on a schedule, so volume grows
  daily and the partitioned bronze layout is built to scale; (b) land verbose **raw**
  responses (incl. Trends `interest_by_region` + related queries) honestly rather
  than fabricating; (c) **synthetic augmentation is the explicit rubric-sanctioned
  fallback** if we must demo at scale — preferred order is real-first, synthetic only
  to backfill gaps we can't get (e.g., deep price history), clearly labeled.
- **🚩 Orchestration: the rubric names Airflow** (a learning goal *and* a final-demo
  component). Current stack uses **Cloud Scheduler → Cloud Run/Functions**, which is
  a defensible "equivalent," but we likely leave points on the table vs. an actual
  Airflow DAG. **[decide as a team]** options: (a) Cloud Composer (managed Airflow,
  ~$300+/mo — heavy for a class project), (b) lightweight local/Docker Airflow whose
  DAG just *triggers* our Cloud Run jobs (cheap, gives the DAG screenshot the demo
  wants), (c) argue Scheduler-as-orchestrator. Our jobs are being built idempotent +
  parameterized so any of these can drive them.
- **Bonus = Terraform** (not Great Expectations). All new infra is Terraform-first.

## Architecture (medallion on GCP)

```
APIs ──► BRONZE (raw, GCS)         ──► SILVER (cleaned, BigQuery + processed/ GCS) ──► GOLD (features, BigQuery/analytics)
         gs://…-raw/<source>/           per-source typed tables, deduped/upserted        model-ready fact + dim tables
         dt=YYYY-MM-DD/…json             (e.g. tm_events MERGE on event_id)               (e.g. fact_event_demand)
```

- **Bronze:** untouched API JSON, Hive-partitioned `gs://…-raw/<source>/dt=<date>/…`
  via `common/gcs_io.upload_raw(source=…)`. Append-only snapshots = replay + history.
- **Silver:** typed, deduped per-source tables. Ticketmaster already does this
  (`tm_events`, upsert MERGE keyed on `event_id`, exported to `processed/` as
  Parquet). Trends and YouTube mirror the pattern.
- **Gold:** the joined, model-ready star schema (below).

## Schema design sketch (star; rubric M2 + final)

OLAP / star schema, grain = **one upcoming event**:

- **`fact_event_demand`** (grain: event × snapshot_date) — `event_id`, `artist_id`,
  `venue_id`, `dma`, `local_date`, `days_to_show`, `price_min/max`, `status`,
  `local_interest_index` (Trends), `national_interest` (Trends), `yt_subscribers`,
  `yt_topic_views`, `venue_capacity` *[confirm]*, plus snapshot timestamp.
- **Dimensions:** `dim_artist` (joins YouTube + Trends on artist), `dim_venue`
  (city/state/DMA/lat-lon/capacity), `dim_geo` (DMA ↔ `US-<STATE>-<DMA>` ↔ metro
  name), `dim_date`.
- **Join keys:** Ticketmaster↔Trends on **(artist, DMA, date)**; everything↔YouTube
  on **artist**; venue→DMA via our crosswalk (`google_trends_api/geo/`).

This is what makes the per-source bronze worth landing: the sources only become a
demand model once joined on artist + geography + date.

## Google Trends piece (Tomas — this repo's focus)

- **Roster is derived from Ticketmaster** (silver `tm_events`: artists with upcoming
  shows + the DMAs they play), ranked soonest-show-first, so Trends covers exactly
  the acts the rest of the pipeline tracks. Saved as a dated, committed artifact for
  reproducibility.
- **Efficiency over tricks:** `interest_by_region(DMA)` returns all ~210 DMAs in one
  call; one `interest_over_time` covers a whole ~270-day daily window. Polite 24/7
  pacing, idempotent checkpointed queue.
- **Backfill:** daily resolution within the ~9-month window, soonest-show-first.
  **Ongoing:** a scheduled collector also captures `now 7-d` *hourly* data — the one
  resolution that genuinely can't be backfilled later.
- **Infra:** Terraform-first — Cloud Run Job (backfill) + Scheduler (daily),
  dedicated `gtrends-ingest` SA, monitoring — mirroring `terraform/ticketmaster_*`.
- Details: `google_trends_api/README.md`.

## Tech stack (current + proposed)

- **Ingestion/compute:** Cloud Run Functions (TM) + Cloud Run Jobs (Trends backfill);
  Python 3.11 (`environment.yml`, conda `music-demand`).
- **Storage:** GCS medallion buckets (bronze/silver-processed/gold-analytics);
  BigQuery `event_demand_analytics` (silver/gold tables).
- **Orchestration:** Cloud Scheduler today; **Airflow TBD** (see risks).
- **IaC / bonus:** Terraform. **Secrets:** Secret Manager. **Monitoring:** Cloud
  Monitoring log-based alerts → email.
- **Shared code:** `common/gcs_io.py` (bronze landing); `tests/` (pytest).

## Division of labor

- **Nicky (`nnaderzad`)** — Ticketmaster pipeline (live, nationwide, deployed),
  shared infra (`common/gcs_io.py`, Terraform, monitoring, tests), YouTube POC.
- **Tomas (`tkovalcik`)** — Google Trends ingestion (this branch): roster, geo
  crosswalk, backfill + ongoing collectors, Terraform, docs.
- **YouTube teammate** — YouTube collection at scale **[needs to start snapshotting —
  history is being lost daily; see team_messages.md]**.
- **noamsfein** — original Ticketmaster POC. **[confirm current role with team]**

## Open questions for the team

1. Orchestration: Airflow (which flavor) vs. Cloud Scheduler? (affects demo points)
2. Venue **capacity** source — Ticketmaster doesn't give it; where from?
3. Do we pursue a historical **resale-price** dataset, or accept forward-only +
   defend the scale story with temporal accumulation (+ labeled synthetic)?
4. Confirm star-schema fact/dim split before M2 ER diagram.
