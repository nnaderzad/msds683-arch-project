# Team Build Roadmap

A shared, technical-only roadmap to take the project from "sources land raw data"
to a working **end product**: a React app + live API that lets a user pick a show
and see its Google Trends / YouTube / ticket-price history plus a price forecast to
show date — built straight off the **gold** layer, validated at every layer.

> Scope: technical build only. Slides, script, and demo choreography are tracked
> separately. Orchestration (Airflow) is **not** required.

---

## How to use this board

- **One shared pool — pull what interests you.** There is no per-person
  assignment. Take any task whose **prereqs are all done**.
- When you **start** a task, put your initials in its `Owner` field. When it's
  **done** (tests green in CI, PR merged), check its box `- [x]`.
- **One task = one small PR**, scoped to its own directory/module (see layout) so
  PRs stay conflict-free. `git pull origin main` before you start anything.
- **Coordinate before touching shared hotspots:** `terraform/`, `common/`, and any
  shared SQL. One owner per PR there; announce in the group chat.

### Definition of done (every task)
Code **+** its unit tests **+** the relevant Great Expectations suite all green in
CI (`.github/workflows/ci.yml`), and the PR is merged to `main`. A task is not
"done" just because the happy path runs once locally.

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
analysis/            tm_price_eda.py  (curates the demo-ready show list)
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

### Phase 0 — Foundations  *(all ready now, no prereqs)*

- [ ] **T0 · Test harness & seed fixtures**  ·  Owner: `____`
   - Prereqs: none — ready
   - Build: a tiny committed sample slice (a handful of events/artists across all
     three sources) + shared pytest fixtures; confirm CI runs them.
   - Tests / done-when: fixtures import clean; `pytest` collects them; CI green.

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

- [ ] **H1 · Ticketmaster price-coverage EDA → demo-ready show list**  ·  Owner: `____`
   - Prereqs: none — ready
   - Build: `analysis/tm_price_eda.py` (read-only, deterministic; extends
     `analysis/profile_schema.py`): which shows have price + trends + youtube
     coverage → emit a curated **demo-ready show list** the UI prioritizes.
   - Tests / done-when: unit test on the coverage/scoring function over a fixture.

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
   - Prereqs: H1, B1
   - Build: finalize the high-quality shows the UI defaults to.
   - Tests / done-when: list committed; each show verified to render in the app.

---

## Dependency quick-reference (what's unblocked)

- **Ready now:** `T0`, `C1`, `G0`, `F1`, `H1`, `E1` (stub).
- **After `T0`:** `A1`, `A2`, `A3` → then `C3`, `INT-1`.
- **After `C1`:** `C2`, `C3`.
- **After `A1`+`A2`+`A3`:** `B1` → then `C4`, `D1`, `INT-2`, `G1`.
- **After `B1`:** `D1`; **after `D1`+`B1`:** `D2` → then `E2`, `INT-3`, `F3`.
- **After `F1`:** `F2`; **after `E2`+`F2`+`H1`:** `F3`.
- **Last:** `E2E-1` → `SCALE-1`, `I1`, `I2`.
- **Shared hotspots (one owner per PR, announce first):** `terraform/`, `common/`, shared SQL.
