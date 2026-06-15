# Midterm Design Pitch — prep pack

*MSDS 683 · M3 (~7 min + QnA). Grading is on **clarity of ideas, not finished
code**. This folder is the team-facing prep; build the slides from it. Last
updated 2026-06-15.*

## Contents
- **`schema_decision.md`** — processing paradigm + schema (sections 2 & 3 below).
- **`transformation_pipeline.md`** — the raw→gold flow (section 3).
- **`tech_stack.md`** — tools/services diagram (section 4).
- **Evidence:** `../../analysis/profile_schema.py` → `../../analysis/output/schema_profile.md`
  (real-warehouse numbers behind every claim; re-runnable).

## The numbers to memorize (from real data)
- **Venue → DMA join: 99.5%** → our geographic thesis is achievable.
- **Ticketmaster price coverage: 23%** (face value) → why we're adding SeatGeek.
- **Dance/Electronic price coverage: 51%** → EDM = a clean optional demo lens.
- **36,251 events / 3,216 venues** today, accumulating daily (temporal richness).

---

## Slide-by-slide outline (~7 min)

### 1. Domain & business goal (~1 min)
- **Domain:** live-music event demand. **Goal:** anticipate, for an upcoming show,
  whether it will sell out / how resale price will move — early enough to act.
- **4 analytical use cases:** (1) sell-out / price-pressure score per event;
  (2) local-vs-national popularity gap; (3) price trajectory vs. interest trajectory;
  (4) genre/metro demand heatmap.
- **Thesis in one line:** *demand is high when a locally-popular artist plays a
  relatively small venue.* → needs event + **local** popularity + capacity.

### 2. Processing paradigm & schema (~2 min) — *the core*
- **OLAP, daily batch** (not OLTP): analytical aggregation over accumulating
  snapshots; no transactions. Process yesterday's data each morning; idempotent.
- **Schema = a simple star** (`fact_event_demand` + `dim_artist`, `dim_venue`,
  `dim_geo`, `dim_date`). **Draw this.**
- **The honest nuance (one sentence):** the 3 sources arrive at *different grains*,
  so under the star sit two small daily fact tables (interest @ artist×DMA×day,
  popularity @ artist×day) that the batch rolls up → *constellation underneath, star
  on top.* Why: don't throw away the daily interest trajectory (use case #3).
- *Speaker note:* keep it simple — present the star, mention the helpers in one breath.

### 3. Transformation pipeline (2–3 min)
- Walk the diagram in `transformation_pipeline.md`: **raw JSON (GCS bronze) →
  typed/deduped per-source facts (BigQuery silver) → joined star (gold)**, labeling
  each step (standardize dates, normalize artist name, MERGE-dedupe, DMA crosswalk
  join, LEFT-join sources onto the event spine, derive `days_to_show`/trajectories).
- Stress: **event is the spine, every event/artist kept**; interest/popularity are
  LEFT-join enrichments (NULL until collection reaches that artist).
- Orchestration: Scheduler+Cloud Run for acquisition; **Airflow** for silver→gold.

### 4. Early tech stack (~1 min)
- One diagram (`tech_stack.md`): Cloud Run + Scheduler → GCS bronze → Airflow →
  BigQuery star; Terraform IaC; Secret Manager; Cloud Monitoring; GitHub Actions CI.
- Note: most of it is **already deployed** (TM/Trends/YouTube live; SeatGeek POC in
  progress).

---

## QnA prep (≥2 questions guaranteed — one from the instructor, one peer)

- **Why OLAP not OLTP?** No transactional writes; the workload is read-heavy
  aggregation over daily snapshots → columnar warehouse + star, not a normalized DB.
- **Why a star (with helper facts) vs. one big fact?** A single event×day fact would
  pre-aggregate Trends to one number per event, killing the daily interest trajectory
  use case. Keeping per-grain facts preserves signal and lets each source be built
  independently; the gold star is still simple to present and query.
- **Why not snowflake?** Dims are small/flat (≈3.2k venues); star keeps joins shallow
  and the slide readable. We denormalize; `dim_geo` is the only plausible snowflake
  and we chose not to.
- **How do you reconcile three different grains?** Conformed dimensions (artist,
  venue/DMA, date) + LEFT joins onto the event spine; missing signal = NULL, not a drop.
- **Google Trends is 0–100 *relative* — how can you compare across artists/metros?**
  We don't compare raw values across artists; we keep a per-artist national series to
  normalize for the *gap* use case. (Parked detail for now.)
- **Ticketmaster only prices 23% of events — is the thesis viable?** Two answers:
  (a) it's *face value* anyway; (b) we're adding **SeatGeek** (secondary `lowest_price`
  + `listing_count` = resale demand), which fits the resale thesis better and is a
  fallback. Price columns are source-agnostic in the schema.
- **Where does venue capacity come from?** A **one-off web/Wikipedia gather**, loaded
  as a static `dim_venue` attribute (capacity rarely changes); SeatGeek sometimes
  provides it too.
- **No source has price/popularity history — isn't that a problem?** Correct: TM,
  SeatGeek, and YouTube are all current-snapshot. We build history by **snapshotting
  daily** (temporal richness, already running). Google Trends is the exception — it
  backfills ~9 months of real history.
- **How does this scale to 100 GB+?** Partitioned (`dt=`) append-only snapshots grow
  daily across 4 sources + nationwide events; the layout is built to scale, and we
  defend the design rather than padding with synthetic data.
- **Why Airflow if Cloud Scheduler already works?** Scheduler is fine for independent
  acquisition jobs; Airflow is for the silver→gold **dependency graph** (ordering,
  retries, DQ gates) — and it's a rubric learning goal.

## Backlog surfaced during prep (not slides)
- **SeatGeek spike** (verify API access → POC → collector) — now the top thesis-risk item.
- Fallback price/coverage sources if SeatGeek stalls: Eventbrite, Bandsintown,
  Resident Advisor.
- Parked schema details: surrogate keys, SCD types, Trends cross-artist normalization.
