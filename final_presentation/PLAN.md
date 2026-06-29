# Final Presentation Plan — Pipeline → End Product Demo (Part 3 focus)

## Context

MSDS683 final presentation + demo (~10 min), due **2026-06-29**, graded out of 60 points
across 6 rubric items. This doc grounds the presentation in the **actual repo state** vs. the
team plan, goes deep on **Part 3 (Pipeline → End Product Demo, 25 of 60 pts)**, and flags
conflicts between what the repo does today and what the demo needs as **critical items**.

Decisions locked:
- **End product** = the planned **React + live FastAPI app over gold/forecast** (a teammate is
  actively building it; treat as a critical dependency, not done).
- **Demo all three source paths** (TM price, Trends geo, YouTube popularity) in reasonable depth.
- **Demo the mainline merged pipeline.** The uncommitted forward-fill-honesty work on
  `tk/tm-forward-fill-honesty` is **left out** of the presentation entirely.

### Rubric map
| Section | Rubric item | Pts |
|---|---|---|
| Quick recap | (assumed-known; not scored directly) | — |
| Schema — what changed | Schema changes clearly explained | 5 |
| **Pipeline → demo** | **Transformations = business logic + ≥1 demoed** | **15** |
| **End product** | **Output lands in consumable, demoable form** | **10** |
| Tech stack | Tech stack appropriately justified | 10 |
| Budget & cost | Realistic estimates with stated assumptions | 5 |
| Whole talk | Presentation clarity + demo quality | 15 |
| | **Total** | **60** |

---

## Actual repo state (ground truth)

**Bronze** (raw JSON in GCS `gs://data-architecture-498123-raw/`, dt-partitioned, append-only):
- Ticketmaster — Cloud Run function, every 4h (6 runs/day), 50 states + DC → `ticketmaster/dt=.../...json` (~7.3 GB).
- Google Trends — Cloud Run job → `google_trends/dt=.../...json` (national daily, DMA snapshot, daily series) (~134 MiB).
- YouTube — daily collector → `youtube/dt=.../...json` (~100 KiB).

**Silver** (BigQuery `data-architecture-498123.event_demand_analytics`):
- `fact_ticketmaster` — event × snapshot_date — 609,728 rows (dbt model).
- `fact_trends` — artist × DMA × snapshot_date — 216,510 rows (snapshot grain).
- `fact_trends_daily` — artist × DMA × day — 1,581,872 rows (built, **not yet wired into gold**).
- `fact_youtube` — artist × snapshot_date — 4,534 rows.
- Conformed dims: `dim_date`, `dim_artist`, `dim_venue`, `dim_geo` (DMA spine), `dim_event`, `bridge_event_artist`.

**Gold** (same dataset):
- `fact_event_demand` — event × snapshot_date star — 609,728 rows / 9,657 events; TM spine
  LEFT-JOIN trends + youtube on headliner. No-row-drop invariant (GX-tested).
- `forecast_event_price` — 494,961 rows, deterministic seeded `HistGradientBoostingRegressor`, $0.74–$92.27.

**Coverage reality (from `queries/hero_shows.sql`, see hero-shows.md):** of 40,770 events only
**9,661 are priced** and only **147 have full coverage** (price+trends+youtube); **6 in Bay Area.**

**Quality / orchestration:** Great Expectations (15 checkpoints); Cloud Run gold-refresh job
chaining silver → dbt build → forecast → GX gate daily.

**End product (the gap):** `api/app.py` returns hardcoded stubs (E2 not done); no `web/` React
app (F1/F3 not started); only existing visual artifact is `eda/output/` (static md + 16 PNGs).

---

## CRITICAL ITEMS — close before the demo (ranked by risk)

1. **[BLOCKER] Live app not built — 10 pts.** Land **E2** (FastAPI → BigQuery reads of
   `fact_event_demand` + `forecast_event_price`) and **F1/F3** (React scaffold + charts +
   dropdown). Set a hard go/no-go tonight + a fallback (below).
2. **[BLOCKER] Hero-show curation.** Only 147 full-coverage events (6 Bay Area). Pick 1–3 from
   the curated pool in **hero-shows.md** and freeze IDs. Recommended: **Everclear @ The
   Independent** (`rZ7HnEZ1Af00jd`); nationwide backup **The Wallflowers**.
3. **[HIGH] Interest time-series lives in `fact_trends_daily`, gold joins `fact_trends`
   (snapshot, sparse 3–6 pts).** Point the interest chart at `fact_trends_daily` for a fuller line.
4. **[HIGH] Forecast freeze.** `forecast_event_price` must match the demoed gold. If the pipeline
   re-runs tonight, re-run forecast — or freeze a known-good snapshot and don't touch it.
5. **[MED] Live vs. localhost + offline fallback.** Decide deploy (G2) vs localhost; record a
   screen capture / screenshots of the working app as backup.
6. **[MED] Demo a real transformation live.** Recommended `dbt build -m fact_event_demand` + a
   `bq query` for the hero show; rehearse to a few seconds.
7. **[LOW] Honesty guardrail.** Mainline gold is forward-filled — describe price as "latest
   observed face value," not a daily tick. (Fwd-fill audit is intentionally out of scope.)
8. **[LOW] Genre framing.** Collection is now nationwide/all-genre (`primary_genre` often
   "Other"/null), not strictly electronic. Soften recap wording or lean on the Bay-Area angle.

**Fallback if the app slips (protects the 10 pts):** a **Looker Studio** report wired directly to
BigQuery gold + forecast (free, no code, ~1–2 hrs): show picker + price/forecast line +
local-interest by metro. Last resort: existing `eda/output/` plots (weakest — historical).

---

## Full presentation outline (~10 min, ~13 slides)

| # | Slide | Time | Rubric |
|---|---|---|---|
| 1 | Title — project, team, one-line domain | — | — |
| 2 | Quick recap — domain + business question | 30s | — |
| 3 | Schema — what changed | 30s | 5 |
| 4 | Part 3 opener — architecture map | — | 15/10 |
| 5 | Bronze — raw from all 3 sources | ~50s | 15 |
| 6 | Silver — the constellation | ~70s | 15 |
| 7 | Live transformation demo | ~50s | 15 |
| 8 | Gold — the star + forecast | ~60s | 15 |
| 9 | (optional) Data-quality gates (GX) | ~20s | 15 |
| 10 | End product — the live app | ~90s | 10 |
| 11 | Tech stack diagram + per-tool role | 1–2 min | 10 |
| 12 | Budget & cost model | 1–2 min | 5 |
| 13 | Close — what's next + bonus (orchestration) | — | — |

### Slide 2 — Quick recap (30s)
- Domain: Bay-Area / nationwide live-music events.
- Thesis: **"Demand is high when a *locally-popular* artist plays a *relatively small* venue."**
  Goal: help a buyer/seller/analyst anticipate sell-out + price pressure **early enough to act.**

### Slide 3 — Schema, what changed (30s)
- Core schema **unchanged** from the midterm: silver fact-constellation → gold star
  (`fact_event_demand`). Say so plainly.
- Added since midterm: (a) `fact_trends_daily` daily-trajectory fact + 250-artist roster
  retarget; (b) materialized `forecast_event_price` in gold (precomputed, deterministic);
  (c) YouTube popularity signal.

---

## PART 3 DEEP DIVE — Pipeline → End Product (~5 min)

> Spine: **follow ONE hero show** (Everclear @ The Independent) from a raw JSON blob to a
> price+forecast chart in the app; enrich the same show with the other two sources so all
> three paths converge on one story.

### Slide 4 — Architecture map ("you are here")
- One diagram: `3 sources → Bronze (GCS raw JSON) → Silver (BQ facts+dims) → Gold (BQ star +
  forecast) → Live app`. Light up the hero-show path; point back to it each step.

### Slide 5 — Bronze: raw, untouched (≈50s)
Show actual raw payloads (`gsutil cat | head` or GCS console):
- **TM JSON** — `id`, `name`, `dates.start`, `_embedded.venues`, `priceRanges[].min/max/currency`.
  Append-only, 6 captures/day per state.
- **Trends JSON** — `ibr_DMA`: `geoCode (DMA)`, `value (0–100)`, query=artist, date.
- **YouTube JSON** — `subscriberCount`, `viewCount`, `videoCount` per artist/day.
- Business framing per source: TM = which shows / where / when / face price (spine + target);
  Trends = how big the artist is **in this metro**; YouTube = how big the artist is **overall.**

### Slide 6 — Silver: clean + conform into a constellation (≈70s)
- **`fact_ticketmaster`** (event × snapshot_date): parse `priceRanges` → `price_min/max`; derive
  `days_to_show`; dedupe to one row per (event, day); deterministic surrogate keys. *(dbt — show SQL.)*
- **`fact_trends`** (artist × DMA × day): normalize to artist_id + dma_code, `interest` 0–100.
  Caveat aloud: 0–100 is **per-pull** — comparable across time for one (artist, metro), never across.
- **`fact_youtube`** (artist × day): subscribers + views.
- **Conformed dims** = join glue: `dim_geo` (Nielsen DMA — the only geography TM venues and Trends
  share; venue→DMA ~99.5%), `dim_date`, `dim_artist`, `dim_venue`, `dim_event`, `bridge_event_artist`.
- Why a constellation: each fact keeps its **native grain** instead of flattening prematurely.

### Slide 7 — Transformation demo (the required "≥1 transformation demoed", ≈50s)
- **Recommended:** `dbt build -m fact_event_demand` → then `bq query` the hero show, showing TM
  price + joined `local_interest` + `yt_subscribers` in one wide row. Reinforces ELT-in-warehouse.
- Say the logic aloud: *"spine = every observed event-day; LEFT-join local search interest +
  global YouTube reach onto it — no event-day is ever dropped."*

### Slide 8 — Gold: the star + the forecast (≈60s)
- **`fact_event_demand`** (609K rows / 9,657 events): one wide model-ready row per event per
  snapshot. Spine kept whole (no-row-drop, GX-tested). Show the hero-show row.
- **`forecast_event_price`** (494,961 rows): demand/price model **precomputed into gold** —
  deterministic, seeded, pooled cross-sectional (honest given ~2 weeks of history; not per-show
  ARIMA). This is what the app reads.

### Slide 9 — Data-quality gates (optional, ≈20s)
- Great Expectations between layers (15 checkpoints); gold-refresh **fails fast** if the spine is
  dropped or counts go wrong.

### Slide 10 — END PRODUCT: the live app (≈90s, the 10-pt moment)
1. Open the app. 2. Pick a hero show from the dropdown (from `fact_event_demand`). 3. Show:
   price history + forecast line; local interest by metro/over time (`fact_trends_daily`);
   YouTube reach. 4. Takeaway: *"One screen — popular here? small room? price trending up with
   days-to-show? → act now or wait."* Closes the loop to Slide 2.

---

## Slides 11–13 (rougher — fill with team)

### Slide 11 — Tech stack (1–2 min, 10 pts)
Diagram + each component's role; be ready to justify any one tool + alternatives:
- **GCS** — cheap durable raw landing. **BigQuery** — serverless warehouse for silver/gold.
- **dbt-bigquery (ELT)** — transforms run *in* the warehouse; one place for DDL, incremental
  materialization, tests, lineage. *Alt:* in-memory pandas joins (don't scale). ← likely probe target.
- **Cloud Run + Cloud Scheduler** — scale-to-zero scheduled compute. *Alt:* Airflow (deferred; bonus).
- **Great Expectations** — declarative DQ gates. *Alt:* hand-rolled asserts.
- **scikit-learn `HistGradientBoostingRegressor`** — deterministic, handles categoricals, fits pooled design.
- **FastAPI + React/Vite** — live API over gold + UI. **Terraform** — reproducible GCP IaC. *Alt:* click-ops.
- **GitHub Actions CI** — ruff + pytest + terraform validate + GX smoke.

### Slide 12 — Budget & cost (1–2 min, 5 pts) — CONFIRM REAL NUMBERS FROM GCP BILLING
State assumptions tied to volume / query frequency:
- **GCS storage** ~7.5 GB today (TM dominates) @ ~$0.02/GB-mo ≈ <$1/mo; project growth.
- **BigQuery storage** ~1–2 GB ≈ <$1/mo. **BQ query** $6.25/TB on-demand; partition+cluster → single-digit $/mo.
- **Cloud Run + Scheduler** scale-to-zero → ~free-tier to a few $/mo. **Secret Manager/monitoring** negligible.
- **Rough total ~$5–20/mo (~$60–240/yr)** at current volume; dominated by storage growth + query freq.
  *Flag every number as an assumption; replace with billing-console actuals.*

### Slide 13 — Close + bonus
- One line on what's next (richer geo signal wired to gold; deploy hardening).
- **Bonus:** team built real orchestration (Cloud Run gold-refresh chaining silver→dbt→forecast→GX
  gate). Per the assignment, orchestration already built can count toward bonus — **talk to the
  instructor** to claim it.

---

## Build checklist for tonight
1. Curate hero shows — see hero-shows.md; freeze IDs. *(blocker)*
2. Land E2 — `api/app.py` reads BigQuery gold + forecast. *(blocker)*
3. Land F1/F3 — React scaffold + dropdown + charts wired to API. *(blocker)*
4. Point interest chart at `fact_trends_daily`; confirm hero data. *(high)*
5. Freeze forecast consistent with demoed gold. *(high)*
6. Rehearse the live transformation. *(med)*
7. Record a backup screen capture / screenshots. *(med)*
8. Stand up Looker Studio fallback. *(insurance)*
9. Dry-run the full 10 min once.

## Verification before presenting
- `bq query` each hero show in `fact_event_demand` + `forecast_event_price` → non-null price + interest + forecast.
- Hit the live API for a hero `event_id` → returns gold data, not stubs.
- Click the dropdown → every hero show renders all charts with data.
- Time a full run-through; live transformation returns in seconds.
