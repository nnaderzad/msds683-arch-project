# Team Build Roadmap

A shared, technical-only roadmap to take the project from "sources land raw data"
to a working **end product**: a React app + live API that lets a user pick a show
and see its Google Trends / YouTube / ticket-price history plus a price forecast to
show date — built straight off the **gold** layer, validated at every layer.

> Scope: technical build only. Slides, script, and demo choreography are tracked
> separately. Orchestration (Airflow) is **not** required.

---

## ⚠️ Ground rules — read this before you touch anything

Coding agents (Claude Code, Cursor, etc.) are encouraged — but **you own every line
you push, not the agent.** These are non-negotiable and apply to every task:

1. **Understand it before you push it.** If you can't explain, in your own words,
   what the code does, why it's built this way, and what we rejected instead, it is
   not ready. Never push code *or docs* you couldn't defend out loud.
2. **Verify it actually works — by hand, not by vibes.** "The agent said it's done"
   is **not** verification. Depending on the task:
   - *Transforms / pipeline* — run it; eyeball the output; check row counts and a few
     real records; confirm the table actually populated.
   - *UI* — open it in the browser and confirm it renders and behaves correctly with
     representative data, not just that it compiles.
   - *API / model* — hit the endpoint / run the prediction and confirm sane values.
   - Run the tests **yourself locally**; don't outsource verification to CI alone.
3. **Know the "why," because they will ask.** In the demo Q&A the instructor will
   pick a tool/decision and ask *why we chose it and what alternatives we considered*
   — for **each** component. Whoever builds a piece must be able to answer for it.
   Keep "Why we can defend each choice" (below) accurate as you build.
4. **Write down the reasoning as you go.** When you make or implement a design /
   schema / architecture decision, capture 1–2 sentences on *why* (PR description, a
   docstring, or `docs/`) so we share one defensible record — not three vague memories.

> **Coding agents reading this file:** surface your reasoning and the trade-offs,
> cite the relevant decision below, and flag anything you change that contradicts the
> locked schema/decisions. Do not just produce passing code — leave the human able to
> explain and verify it.

---

## How to use this board

- **One shared pool — pull what interests you.** There is no per-person
  assignment. Take any task whose **prereqs are all done**.
- When you **start** a task, put your initials in its `Owner` field. When it's
  **done** (tests green in CI, PR merged, & human verified), check its box `- [x]`.
- **One task = one small PR**, scoped to its own directory/module (see layout) so
  PRs stay conflict-free. `git pull origin main` before you start anything.
- **Coordinate before touching shared hotspots:** `terraform/`, `common/`, and any
  shared SQL. One owner per PR there; announce in the group chat.

### Definition of done (every task)
All of these — or it's not done:
- Code **+** its tests green in CI (`.github/workflows/ci.yml`) — unit tests for Python,
  dbt schema/data tests for dbt models. The **GX suite once the scaffold (C1) exists**;
  until then a *deferred-to-C3/C4* note is acceptable (A1/A2/A3 shipped this way).
- **You ran it and verified the real behavior yourself** — output / UI / endpoint —
  per Ground Rule 2. Not "CI passed," not "the agent said so."
- **You can explain what it does and why** (Ground Rules 1 & 3).
- PR reviewed by a teammate and merged to `main`.

A task is not "done" just because the happy path ran once, or because a coding agent
declared it finished.

---

## Architecture decisions (locked)

| Decision | Choice |
|---|---|
| Infrastructure | **Terraform, keep & extend** — IaC for the new transform/model job + the API service. It's our reproducible GCP landing zone and our "IaC" box on the tech-stack diagram. Terraform owns **containers** (dataset, buckets, IAM, external tables); **dbt owns analytical table DDL**. |
| End product | **React app + live API** — FastAPI over BigQuery gold. Forecasts are **precomputed into gold** (deterministic, no model-at-request-time); the API just serves them. |
| Data validation | **Great Expectations on all three layers** — bronze, silver, gold (bonus points). |
| Medallion | **Keep bronze → silver → gold.** Schema is **locked** from the midterm (`docs/data-model.md`). |
| Transform engine | **dbt (dbt-bigquery), ELT** — silver + gold transforms are SQL models pushed down to BigQuery; dbt owns table DDL, materialization, tests & lineage. Python ingests raw + orchestrates. *(A1/A2/A3 predate this and migrate via `MIG-1/2/3`.)* |

**Locked schema (from `docs/data-model.md`):**
- **Silver = constellation:** three native-grain facts — `fact_ticketmaster`
  (event × snapshot_date), `fact_trends` (artist × dma × snapshot_date),
  `fact_youtube` (artist × snapshot_date) — plus conformed dims
  `dim_date / dim_artist / dim_venue / dim_geo / dim_event` and `bridge_event_artist`.
- **Gold = star:** `fact_event_demand` keeps the Ticketmaster event-snapshot spine
  (`fact_ticketmaster`, event × snapshot_date) and **LEFT-JOINs** the other source facts
  onto it — `fact_trends` on (headliner `artist_id`, venue `dma_code`, `snapshot_date`),
  `fact_youtube` on (headliner `artist_id`, `snapshot_date`). Every event row is kept;
  missing signals are NULL (never filtered out).

### Why we can defend each choice (Q&A prep — keep this current)

One line each: *what we chose — why — what we rejected.* If you build a piece, make
sure its row is true and you can expand on it live.

- **Medallion (bronze→silver→gold)** — separates a replayable raw audit log from
  cleaned/conformed and model-ready data, so each layer is testable and debuggable in
  isolation. *Rejected:* one flat table (no lineage, no replay).
- **Silver constellation + gold star** — keeping each source at its native grain
  preserves the daily interest *trajectory* (artist × DMA × day) the demand thesis
  needs; the gold star then keeps the model join shallow. *Rejected:* a single star
  (pre-aggregates Trends and throws away the signal); a snowflake (negligible storage
  win on tiny dims, extra joins).
- **Ticketmaster as the price/event anchor** — free, no approval, broad coverage, and
  `externalLinks` give us artist join keys. *Rejected:* SeatGeek (resale prices gated
  behind partner approval); scraping (blocked / ToS).
- **DMA as the geo spine** — Trends reports by Nielsen DMA and venue→DMA resolves
  ~99.5%; it's the only geography both sources share. *Rejected:* state/city (coarser,
  doesn't match Trends granularity).
- **Terraform (IaC)** — reproducible, reviewable, one-command stand-up/tear-down of the
  GCP landing zone. *Rejected:* click-ops (not reproducible); imperative `gcloud`
  scripts (drift).
- **dbt (dbt-bigquery) for silver/gold transforms** — the join/aggregation runs *in* the
  warehouse (ELT push-down), so it scales as snapshots accumulate instead of pulling rows
  out to one Python node; dbt gives table DDL, incremental loads, data tests, and
  lineage/docs in one place. *Rejected:* per-table Python in-memory joins (don't scale —
  move data to the compute); a hand-rolled SQL runner (slowly reinvents a worse dbt).
- **React + live FastAPI over gold** — the API *is* the consumable gold-layer product,
  decoupled from the warehouse. *Rejected:* a BI/Streamlit dashboard ("just a
  dashboard," less control); static export (no live querying).
- **Forecast precomputed into gold** — deterministic, re-runnable, no model-at-request
  surprises; the API stays thin and testable. *Rejected:* running the model per request
  (nondeterministic, slower, harder to test).
- **Great Expectations on all three layers** — declarative, documented data-quality
  gates with a shared report (and bonus points). *Rejected:* pandera/pydantic (code-level,
  no data-docs artifact); hand-rolled asserts (no shared report).
- **Pooled (cross-sectional) forecast, not per-show ARIMA** — only ~2 weeks of snapshot
  history exists, too thin for per-show time series; pooling across shows + exogenous
  signals is honest and defensible. *Rejected:* per-show ARIMA/Prophet (not enough history).

**Where we are today:**

| Layer | Ticketmaster | Google Trends | YouTube |
|---|---|---|---|
| Bronze (raw JSON → GCS) | ✅ deployed | ✅ deployed | ✅ deployed |
| Silver (BigQuery) | ✅ `fact_ticketmaster` (A4, dbt); `tm_events` current-state → dims | ✅ `fact_trends` (A1) | ✅ `fact_youtube` (A2) |
| Dims + bridge | ✅ `dim_*` + `bridge_event_artist` (A3, from `tm_events`) |||
| Gold (`fact_event_demand`) | ✅ `fact_event_demand` (B1, dbt) — signals fill in with collection coverage (COLLECT-1) |||

> `tm_events` (current-state, MERGE-upsert) feeds the **dims** (A3). The silver TM **fact**
> `fact_ticketmaster` — event × snapshot_date price *history*, sourced from the processed
> parquet snapshots — is a **different table**, built in **A4**; it's the spine B1 joins onto.

> **Honest model constraint:** daily snapshots only started ~mid-June, so per-show
> price *history* is thin (~2 weeks). The forecast must **pool across shows**
> (cross-sectional/panel), not fit per-show ARIMA. Document this assumption.

---

## Testing strategy (every phase)

All deterministic and re-runnable — no LLM at runtime, fixed seeds, committed scripts.

1. **Unit** — pytest, extending the existing `tests/` (`conftest.py`,
   `test_*.py`). Fixture-driven, no network: transform functions on sample JSON,
   feature builders, API handlers.
2. **Data-quality (Great Expectations)** — suites on **bronze** (raw-JSON landing
   shape), **silver** (schema, nulls, value ranges e.g. `interest` ∈ [0,100],
   join-key integrity), and **gold** (`fact_event_demand` integrity, forecast
   sanity). Runs in CI and before each load.
3. **Integration** — exercise one layer boundary on a small **seeded slice**
   (task `T0`): bronze→silver, silver→gold, gold→model→forecast, API↔gold.
4. **End-to-end** — full path on one demo show (bronze sample → silver → gold →
   model → API → UI smoke). One green E2E run gates the demo.

---

## Directory layout (new code lands here — modules stay disjoint)

```
dbt/                 dbt-bigquery project — models/silver · models/gold · sources · tests
pipeline/silver/     Python extractors (A1/A2/A3) — migrating to dbt models (MIG-1/2/3)
pipeline/gold/       export_predictions_table.py  (gold facts are now dbt models)
model/               features.py · train.py · predict.py
api/                 app.py (FastAPI) · Dockerfile
web/                 React (Vite) app
great_expectations/  GX project (suites per layer)
terraform/           extend: api Cloud Run service · dbt transform/model job · dataset/IAM/external tables
eda/                 tm_price_eda.py · profile_schema.py · _common.py (deterministic EDA)
tests/               extend existing pytest suites + shared fixtures
docs/                pipeline_walkthrough.md
```

---

## Task board

Each task:

```
- [ ] **ID · Title**  ·  Owner: `____`
   - Prereqs: <task IDs, or "none — ready">
   - Build: <what to build>
   - Tests / done-when: <unit + GX + integration expectations>
```

> Each task's **Tests / done-when** is *on top of* the universal Definition of Done
> above — including hands-on verification and being able to explain the change.

### ⭐ HIGH PRIORITY — Source collection coverage

The pipeline now runs end-to-end, but **gold demand-signal is capped by ingestion
coverage, not the warehouse**: only ~281 Trends artists and ~656 YouTube artists are
collected vs ~40k events, and Ticketmaster + Trends are both rate-limited. Lifting
coverage where it matters is the single biggest lever on demo/model quality — so this
sits above the medallion build tasks.

- [ ] **COLLECT-1 · Prioritized, continuous source collection (TM + Trends)**  ·  Owner: `____`  ·  ⭐ HIGH
   - Prereqs: none — ready (improves the already-deployed collectors)
   - Build:
     - **Priority queue** — rank events/artists by **historical price/signal consistency**
       (reuse `eda/tm_price_eda.py` `price_completeness`) and spend the existing call-budget
       headroom (a run uses ~659 of 780 calls; ~4,680 of 5,000/day) re-fetching the
       high-value set first by id (`events/{id}.json`), guaranteeing **daily continuity** for
       demo-grade shows. Mirror the Google Trends roster/queue (`google_trends_api/`).
     - **TM tail** — finer slicing / per-genre or per-market filters in dense states so the
       1,000-event-per-slice deep-paging cap stops dropping events.
     - **Trends coverage** — grow the collected-artist roster (281 today) toward the events
       that actually have shows + price history; keep the per-pull 0–100 discipline.
     - **Prune post-show** events (`show_date + ~3d`) from `tm_events`/exports — removes the
       ~7% negative-`days_to_show` rows and shrinks the daily snapshot. Fix the stale
       "twice a day" docstring in `cloud_functions/ticketmaster_daily/main.py` (it's 6×/day).
   - Tests / done-when: measurably higher event-signal coverage in `fact_event_demand`
     (rows with non-NULL `local_interest` / `yt_*`); daily continuity for a curated demo
     set; total call budget stays < 5,000/day. Reasoning notes live with the team.

### Phase 0 — Foundations  *(all ready now, no prereqs)*

- [x] **T0 · Test harness & seed fixtures**  ·  Owner: `TK`  ·  ✅ PR #12
   - Prereqs: none — ready
   - Built: `tests/fixtures/build_seed.py` (deterministic, ADC-gated; reuses
     `eda/tm_price_eda.py`) carves a small **real** slice — 5 artists with a
     complete 16-day TM price series, present in **all three** sources (4 Bay Area
     + 1 national) — into `tests/fixtures/seed/` with a provenance `manifest.json`.
     Shared pytest fixtures in `tests/conftest.py`; `tests/test_seed_fixtures.py`
     unit-tests the pure selectors **and** asserts the cross-source guarantee
     A1/A2/A3 + INT-1 rely on. Pure-stdlib, offline; refreshable to diff over time.
   - Tests / done-when: ✅ offline (`pytest tests/`, no new deps, CI green).

- [ ] **C1 · Great Expectations scaffold**  ·  Owner: `____`
   - Prereqs: none — ready
   - Build: init `great_expectations/`; wire BigQuery + GCS datasources.
   - Tests / done-when: `great_expectations checkpoint list` runs; a trivial suite
     passes in CI.

- [ ] **G0 · Terraform: gold dataset + IAM (+ external tables)**  ·  Owner: `____`
   - Prereqs: none — ready
   - Build: IaC the **containers** — dataset(s), IAM, and any GCS→BQ external tables the
     models read. **Do not create the analytical tables in Terraform** — dbt materializes
     them (Terraform-managed tables would drift against `dbt build`).
   - Tests / done-when: `terraform plan` clean; `bq ls` shows the dataset; dbt can build into it.

- [ ] **F1 · React scaffold (mock data)**  ·  Owner: `____`
   - Prereqs: none — ready
   - Build: Vite React app, artist/show dropdown, chart shell — all on mock JSON.
   - Tests / done-when: component renders with mock JSON; `npm test` + build pass in CI.

- [x] **H1 · Ticketmaster historical price-coverage EDA**  ·  Owner: `TK`  ·  ✅ PR #10
   - Prereqs: none — ready
   - Built: `eda/tm_price_eda.py` (read-only, deterministic; extends
     `eda/profile_schema.py`) — stacks the 15 daily `tm_events` parquet snapshots
     into one panel and measures **per-show price completeness**; emits summaries,
     a `price_type`/status breakdown, and price-trajectory plots (most-complete +
     highest-priced, Bay Area + nationwide) to `eda/output/`.
   - Findings: price history is the bottleneck — only ~24% of events are ever
     priced, ~20% have a complete daily series; **0% resale** (face value only);
     no sold-out flag (`offsale` status is ambiguous). The cross-source
     **demo-ready show list is deferred to I2** (needs silver Trends/YouTube).
   - Tests / done-when: ✅ offline unit tests in `tests/test_tm_price_eda.py`.

### Phase 1 — Silver  *(per source, parallel)*

- [x] **A1 · Trends bronze→silver (`fact_trends`)**  ·  Owner: `TK`  ·  ✅ PR #13
   - Prereqs: T0 ✅
   - Built: `pipeline/silver/trends_to_silver.py` flattens the raw `ibr_DMA`
     (`interest_by_region`) captures → `fact_trends` (artist × dma × snapshot_date),
     `interest` kept 0–100 **per-pull**. DMA comes **natively** from the pytrends
     `geoCode` (no `geo_lookup` needed here — that's venue→DMA for A3). Deterministic
     surrogate keys in `common/keys.py` (`artist_id` = hash of normalized name) so
     A1/A2/A3 build independently yet join. Idempotent staging+MERGE, mirroring the
     `tm_events` pattern; re-runnable via `python pipeline/silver/trends_to_silver.py`.
   - Verified: offline unit tests in `tests/test_trends_to_silver.py` (pure transform
     over the T0 seed) + a real BigQuery load (seed = 4,200 rows, 5 artists × 210 DMAs
     × 11 days, interest ∈ [0,100], PK unique, idempotent on re-run). Full bronze
     backfill (1,031 files) is the same command / future G1 job.
   - **GX silver-trends suite deferred to C3** (no GX scaffold yet — folds in there).

- [x] **A2 · YouTube bronze→silver (`fact_youtube`)**  ·  Owner: `TK`  ·  ✅ PR #14
   - Prereqs: T0 ✅
   - Built: `pipeline/silver/youtube_to_silver.py` flattens the daily YouTube captures
     → `fact_youtube` (artist × snapshot_date): the subscriber/view/video-count
     measures only (channel ids/titles are `dim_artist` attributes per schema change #7,
     so dropped from the fact). Reuses `common/keys.py` `artist_id`; idempotent
     staging+MERGE; hidden subscriber counts preserved as NULL.
   - Verified: offline tests in `tests/test_youtube_to_silver.py` + a real BigQuery load
     (seed = 46 rows, 5 artists × 12 days, subs 2.3k–821k, PK unique, idempotent).
   - **GX silver-youtube suite deferred to C3.**

- [x] **A3 · Conformed dimensions**  ·  Owner: `TK`  ·  ✅ PR #15
   - Prereqs: T0 ✅
   - Built: `pipeline/silver/build_dimensions.py` — `dim_date / dim_artist / dim_venue /
     dim_geo / dim_event` + `bridge_event_artist` from `tm_events` + `reference/dma_geo.csv`
     (= `dim_geo`) and `GeoLookup` (venue→DMA). Pure builders (offline-tested on the seed
     `tm_events`); full-refresh `CREATE OR REPLACE`. Decisions: headliner = name-match
     (else first); `dim_artist` best-effort enriched (roster `query`/`top_genre` + the
     YouTube channel cache for `yt_channel_id`); `is_us_holiday` via the `holidays` lib.
   - Verified: real full load — `dim_event` 40,734 · `dim_artist` 10,356 · `dim_venue`
     3,345 (DMA 99.94%, 2 NULL) · `dim_geo` 210 · `dim_date` 356 · `bridge` 56,506; **PK
     unique** on every table, **0 FK orphans** (bridge→artist, event→venue, venue→geo),
     exactly one headliner/event; 586 artists carry a YouTube channel id.
   - **GX dim suites deferred to C3.**

- [x] **A4 · Ticketmaster bronze→silver (`fact_ticketmaster`)**  ·  Owner: `TK`  ·  ✅ PR #17  ·  *(first dbt model)*
   - Prereqs: T0 ✅
   - Built: stood up the `dbt/` project (dbt-bigquery) **and** `models/silver/fact_ticketmaster.sql`
     — the event × snapshot_date price **history**, sourced from the processed parquet
     snapshots (`gs://<proj>-processed/ticketmaster/*.parquet`, declared as a dbt source via
     dbt-external-tables; `snapshot_date` recovered from `_FILE_NAME`). Schema per
     `docs/data-model.md` §3; `days_to_show` derived. Materialized **incremental**,
     `partition_by=snapshot_date`, `unique_key=tm_snapshot_id` (idempotent). The silver fact
     the gold spine (B1) joins onto — **distinct from current-state `tm_events`**.
   - Verified: real `dbt build` — **609,728 rows = distinct (event_id, snapshot_date)** (PK
     unique), 40,770 events × 16 snapshot days, ~23% priced (matches the H1 EDA), idempotent
     re-run (MERGE 0 rows); dbt tests pass. **GX silver suite → C3; dbt-in-CI → G3.**

- [ ] **C2 · GX bronze suites**  ·  Owner: `____`
   - Prereqs: C1
   - Build: raw-JSON landing checks per source (required keys, types, non-empty).
   - Tests / done-when: suites pass on the T0 sample; **fail** on a corrupted fixture.

- [ ] **C3 · GX silver suites**  ·  Owner: `____`
   - Prereqs: C1 + (A1 | A2 | A3 for the table being validated)
   - Build: schema, nulls, value ranges (`interest` ∈ [0,100]), join-key integrity
     per fact + dim.
   - Tests / done-when: suites green in CI.

- [ ] **INT-1 · Integration: bronze→silver**  ·  Owner: `____`
   - Prereqs: A1, A2, A3, A4, T0
   - Build: run all silver transforms (incl. `fact_ticketmaster`) on the seeded slice end-to-end.
   - Tests / done-when: expected row counts per table; GX silver suites pass.

### dbt adoption — follow-ups  *(new; A1/A2/A3 predate dbt — bring them onto the engine)*

- [ ] **MIG-1 · Migrate `fact_trends` (A1) to a dbt model**  ·  Owner: `____`
   - Prereqs: A4 (dbt scaffold)
   - Build: re-express `pipeline/silver/trends_to_silver.py` as `models/silver/fact_trends.sql`.
     Needs the raw `ibr_DMA` JSON queryable in BQ (GCS→BQ external table / load step) so the
     model can `SELECT` it. Keep `interest` per-pull 0–100; same `artist_id` surrogate.
   - Tests / done-when: dbt tests (PK unique, `interest` ∈ [0,100]); parity vs the current
     Python output on the seed; then retire the Python script.

- [ ] **MIG-2 · Migrate `fact_youtube` (A2) to a dbt model**  ·  Owner: `____`
   - Prereqs: A4
   - Build: `models/silver/fact_youtube.sql` from the YouTube raw (external table / load).
   - Tests / done-when: dbt tests; seed parity; retire the Python script.

- [ ] **MIG-3 · Migrate dims + bridge (A3) to dbt models**  ·  Owner: `____`
   - Prereqs: A4
   - Build: `models/silver/dim_*.sql` + `bridge_event_artist.sql` from `tm_events` (source)
     + `dma_geo.csv` (dbt seed); headliner/enrichment logic in SQL/macros.
   - Tests / done-when: dbt tests (PK unique, FK `relationships`); seed parity; retire
     `pipeline/silver/build_dimensions.py`.

- [ ] **G3 · dbt in CI**  ·  Owner: `____`
   - Prereqs: A4
   - Build: run `dbt build` + `dbt test` against a BigQuery sandbox in CI (service-account /
     WIF creds — **no committed keys**). Decide the **dbt-test vs Great Expectations**
     division of labor so the same checks aren't duplicated in both.
   - Tests / done-when: CI runs dbt green on a sandbox dataset; creds setup documented.

### Phase 2 — Gold + scaffolds

- [x] **B1 · Gold star `fact_event_demand`**  ·  Owner: `TK`  ·  ✅ PR #18
   - Prereqs: A1 ✅, A2 ✅, A3 ✅, A4 ✅
   - Built: `dbt/models/gold/fact_event_demand.sql` — keeps the `fact_ticketmaster` spine
     (`ref`); **LEFT JOINs** `fact_trends` on (headliner `artist_id`, venue `dma_code`,
     `snapshot_date`) and `fact_youtube` on (headliner `artist_id`, `snapshot_date`);
     headliner via `bridge_event_artist.is_headliner`; every event kept (NULL where
     uncollected). Silver facts/dims referenced as dbt `source()`s until `MIG-1/2/3`.
   - Verified: real `dbt build` — gold **609,728 == `fact_ticketmaster`** (no-row-drop test
     passes), grain unique, join value-exact vs source; FK tests pass (warn-level **36-event
     `dim_event` gap** — current-state, see `MIG-3`). After the silver backfill + a gold
     full-refresh: `local_interest` on 22,863 rows / 5,629 events, `yt_*` on 80,049 / 9,577.
     **GX gold suite → C4.**

- [ ] **C4 · GX gold suites**  ·  Owner: `____`
   - Prereqs: C1, B1
   - Build: `fact_event_demand` integrity + (later) forecast sanity — non-negative
     price, `days_to_show` monotone toward show date.
   - Tests / done-when: suites green in CI.

- [ ] **D1 · Model feature build**  ·  Owner: `____`
   - Prereqs: B1
   - Build: `model/features.py` — assemble the training frame from gold
     (days_to_show, interest trajectory, yt signals, capacity, genre, etc.).
   - Tests / done-when: unit on feature shapes + no target leakage, over a fixture.

- [ ] **E1 · FastAPI skeleton**  ·  Owner: `____`
   - Prereqs: none — ready (stub responses)
   - Build: `api/app.py` — `/shows`, `/show/{id}` over gold (stubbed data OK for now).
   - Tests / done-when: pytest via FastAPI `TestClient` on stub responses.

- [ ] **G1 · Terraform: dbt transform Cloud Run job**  ·  Owner: `____`
   - Prereqs: A4/B1 dbt models exist (silver + gold)
   - Build: a Cloud Run job that runs `dbt build` (silver + gold) (extends `terraform/`).
   - Tests / done-when: `terraform plan` clean; dry-run job execution.

- [ ] **INT-2 · Integration: silver→gold**  ·  Owner: `____`
   - Prereqs: B1, T0
   - Build: run the gold build on the seeded slice.
   - Tests / done-when: gold row counts + NULL-handling assertions.

### Phase 3 — Model, live API, web wiring

- [ ] **D2 · Train + predict → gold (`forecast_event_price`)**  ·  Owner: `____`
   - Prereqs: B1, D1
   - Build: `model/train.py` + `model/predict.py` — deterministic pooled regressor
     (e.g. sklearn `HistGradientBoostingRegressor`, fixed seed); per-show daily
     forecast to show date → `forecast_event_price` gold table via
     `pipeline/gold/export_predictions_table.py`. Document thin-history/pooling
     assumptions in a docstring/README.
   - Tests / done-when: unit on **seeded reproducibility** (same input → same
     prediction); GX forecast sanity suite.

- [ ] **E2 · API live endpoints**  ·  Owner: `____`
   - Prereqs: B1, D2, E1
   - Build: serve real per-show history (trends / youtube / price) + forecast; CORS;
     `api/Dockerfile`.
   - Tests / done-when: `TestClient` integration test against a seeded gold table.

- [ ] **F2 · Web charts**  ·  Owner: `____`
   - Prereqs: F1
   - Build: Trends, YouTube, and price-history charts + forecast overlay.
   - Tests / done-when: chart components render on fixture series.

- [ ] **F3 · Wire web → API + demo dropdown**  ·  Owner: `____`
   - Prereqs: E2, F2, H1
   - Build: connect the app to the live API; populate the dropdown from the
     demo-ready show list (H1).
   - Tests / done-when: mocked-fetch integration test; production build passes.

- [ ] **G2 · Terraform: deploy API Cloud Run service**  ·  Owner: `____`
   - Prereqs: E2
   - Build: deploy the FastAPI service (extends `terraform/`).
   - Tests / done-when: `terraform plan` clean; deployed `/health` returns 200.

- [ ] **INT-3 · Integration: gold→model→forecast**  ·  Owner: `____`
   - Prereqs: D2, T0
   - Build: run features→train→predict→export on the seeded slice.
   - Tests / done-when: forecast table row counts + value-range checks.

### Phase 4 — End-to-end & proof

- [ ] **E2E-1 · Full-path smoke test**  ·  Owner: `____`
   - Prereqs: E2, F3, D2, INT-1, INT-2, INT-3
   - Build: scripted run — seeded bronze → silver → gold → model → API → UI.
   - Tests / done-when: smoke asserts the UI shows a forecast for the demo show.
     **One green E2E run gates the demo.**

- [ ] **SCALE-1 · Full-volume pipeline run**  ·  Owner: `____`
   - Prereqs: INT-1, INT-2, INT-3 green
   - Build: run the transforms on all data (the "at scale" data-engineering goal).
   - Tests / done-when: GX suites pass at full volume; runtime/cost noted.

- [ ] **I1 · `docs/pipeline_walkthrough.md`**  ·  Owner: `____`
   - Prereqs: E2E-1
   - Build: document one real bronze→…→UI path with the exact commands + outputs
     (this is the demo narrative's backbone).
   - Tests / done-when: another teammate can reproduce it from the doc.

- [ ] **I2 · Demo-show curation**  ·  Owner: `____`
   - Prereqs: H1 ✅, B1
   - Build: finalize the high-quality shows the UI defaults to.
   - Tests / done-when: list committed; each show verified to render in the app.

---

## Dependency quick-reference (what's unblocked)

- **⭐ HIGH PRIORITY, ready now:** `COLLECT-1` (source coverage — the demand-signal bottleneck).
- **Ready now:** `C1`, `G0`, `F1`, `E1` (stub).  _(`H1`, `T0` ✅ done)_
- **After `T0` ✅:** `A1` ✅, `A2` ✅, `A3` ✅ done; **`A4`** (first dbt model) unblocked → then `C3`, `INT-1`.
- **After `A4`:** `MIG-1`/`MIG-2`/`MIG-3` (migrate A1/A2/A3 to dbt), `G3` (dbt-in-CI).
- **After `C1`:** `C2`, `C3`.
- **After `A1`+`A2`+`A3`+`A4` ✅:** `B1` ✅ done → `C4`, `D1`, `INT-2`, `G1` **now unblocked**.
- **After `B1`:** `D1`; **after `D1`+`B1`:** `D2` → then `E2`, `INT-3`, `F3`.
- **After `F1`:** `F2`; **after `E2`+`F2`+`H1`:** `F3`.
- **Last:** `E2E-1` → `SCALE-1`, `I1`, `I2`.
- **Shared hotspots (one owner per PR, announce first):** `terraform/`, `common/`, `dbt/`, shared SQL.
