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
- Code **+** its unit tests **+** the relevant Great Expectations suite green in CI
  (`.github/workflows/ci.yml`).
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
| Infrastructure | **Terraform, keep & extend** — IaC for the new transform/model job + the API service. It's our reproducible GCP landing zone and our "IaC" box on the tech-stack diagram. |
| End product | **React app + live API** — FastAPI over BigQuery gold. Forecasts are **precomputed into gold** (deterministic, no model-at-request-time); the API just serves them. |
| Data validation | **Great Expectations on all three layers** — bronze, silver, gold (bonus points). |
| Medallion | **Keep bronze → silver → gold.** Schema is **locked** from the midterm (`docs/data-model.md`). |

**Locked schema (from `docs/data-model.md`):**
- **Silver = constellation:** three native-grain facts — `fact_ticketmaster`
  (event × snapshot_date), `fact_trends` (artist × dma × snapshot_date),
  `fact_youtube` (artist × snapshot_date) — plus conformed dims
  `dim_date / dim_artist / dim_venue / dim_geo / dim_event` and `bridge_event_artist`.
- **Gold = star:** `fact_event_demand` left-joins the source facts onto the
  Ticketmaster event spine on **artist × venue-DMA × snapshot_date**. Every event
  is kept; missing signals are NULL (never filtered out).

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
| Silver (BigQuery) | ✅ `tm_events` (MERGE-upsert) | ❌ to build | ❌ to build |
| Gold (`fact_event_demand`) | ❌ designed, to build |||

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
pipeline/silver/     trends_to_silver.py · youtube_to_silver.py · build_dimensions.py
pipeline/gold/       build_fact_event_demand.py · export_predictions_table.py
model/               features.py · train.py · predict.py
api/                 app.py (FastAPI) · Dockerfile
web/                 React (Vite) app
great_expectations/  GX project (suites per layer)
terraform/           extend: api Cloud Run service · transform/model job · gold BQ tables
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

- [ ] **G0 · Terraform: empty gold BigQuery tables**  ·  Owner: `____`
   - Prereqs: none — ready
   - Build: IaC the gold dataset/tables (empty) so downstream tasks have a target.
   - Tests / done-when: `terraform plan` clean; `bq show` confirms tables exist.

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

- [ ] **A1 · Trends bronze→silver (`fact_trends`)**  ·  Owner: `____`
   - Prereqs: T0
   - Build: `pipeline/silver/trends_to_silver.py` — read raw `google_trends/` JSON →
     `fact_trends` (artist × dma × snapshot_date); map DMA via `geo_lookup.py`;
     keep `interest` 0–100 **per-pull** (never average across artists/metros).
     Mirror the `tm_events` Python+BQ pattern; re-runnable via `python -m`.
   - Tests / done-when: unit test on the transform over a T0 fixture; row-count +
     schema assertions; GX silver-trends suite green.

- [ ] **A2 · YouTube bronze→silver (`fact_youtube`)**  ·  Owner: `____`
   - Prereqs: T0
   - Build: `pipeline/silver/youtube_to_silver.py` — `fact_youtube`
     (artist × snapshot_date): subscribers, views, video_count.
   - Tests / done-when: as A1 (unit + schema + GX silver-youtube suite).

- [ ] **A3 · Conformed dimensions**  ·  Owner: `____`
   - Prereqs: T0
   - Build: `pipeline/silver/build_dimensions.py` — `dim_date / dim_artist /
     dim_venue / dim_geo / dim_event` + `bridge_event_artist` from `tm_events` +
     `reference/` crosswalks (venue→DMA is the spine, ~99.5% resolved).
   - Tests / done-when: PK-uniqueness + FK-coverage unit checks; GX dim suites.

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
   - Prereqs: A1, A2, A3, T0
   - Build: run all silver transforms on the seeded slice end-to-end.
   - Tests / done-when: expected row counts per table; GX silver suites pass.

### Phase 2 — Gold + scaffolds

- [ ] **B1 · Gold star `fact_event_demand`**  ·  Owner: `____`
   - Prereqs: A1, A2, A3
   - Build: `pipeline/gold/build_fact_event_demand.py` — left-join silver facts onto
     the TM event spine on (artist_id, venue-dma, snapshot_date); keep every event
     (NULL where uncollected); derive `days_to_show`.
   - Tests / done-when: unit test asserting **no row drop vs. the spine**; GX gold suite.

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

- [ ] **G1 · Terraform: transform/load Cloud Run job**  ·  Owner: `____`
   - Prereqs: A1/A2/A3/B1 scripts exist
   - Build: a Cloud Run job that runs the silver + gold transforms (extends `terraform/`).
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

- **Ready now:** `C1`, `G0`, `F1`, `E1` (stub).  _(`H1`, `T0` ✅ done)_
- **After `T0` ✅:** `A1`, `A2`, `A3` now unblocked → then `C3`, `INT-1`.
- **After `C1`:** `C2`, `C3`.
- **After `A1`+`A2`+`A3`:** `B1` → then `C4`, `D1`, `INT-2`, `G1`.
- **After `B1`:** `D1`; **after `D1`+`B1`:** `D2` → then `E2`, `INT-3`, `F3`.
- **After `F1`:** `F2`; **after `E2`+`F2`+`H1`:** `F3`.
- **Last:** `E2E-1` → `SCALE-1`, `I1`, `I2`.
- **Shared hotspots (one owner per PR, announce first):** `terraform/`, `common/`, shared SQL.
