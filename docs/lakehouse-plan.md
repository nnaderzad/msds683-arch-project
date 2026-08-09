# Lakehouse Build Roadmap (MSDS Data Lakehouse class)

Continuation of the MSDS 683 data-architecture project into the Data Lakehouse course.
Scope: keep the deployed medallion pipeline healthy, add the **Text-to-SQL agent deep
dive**, a **synthetic demand layer**, and the required **performance benchmark** —
then present (final demo **Mon 2026-08-11**, 7 min + 5 min Q&A) and write the blog.
Requirements source: instructor's lakehouse plan (untracked local `notes/`).

## ⚠️ Ground rules — read this before you touch anything

1. **Understand before you push.** Every PR you open, you can explain line by line.
2. **Verify by hand, not by vibes.** A task is done when you watched it work against
   real data (row counts, execution logs, live endpoint), not when the code compiles.
3. **Know the "why" for Q&A.** 9 of 35 presentation points are follow-up questions.
   If you built it, be ready to defend the tool choice and name the alternative we
   rejected (see the Q&A prep section).
4. **Write down reasoning as you go** — decisions land in this doc or `docs/REPO_STATE.md`
   the same day they're made, not the night before the presentation.

> **Coding agents:** read `docs/REPO_STATE.md` first, keep it current with every
> behavior-changing PR, and never source price *history* from `tm_events`.

## How to use this board

One shared pool — nothing is pre-assigned. Put your initials in `Owner` when you
start a task, check it off when done. One task = one small PR scoped to one
directory where possible. Coordinate before touching shared hotspots:
`terraform/`, `common/`, `api/`, gold dbt models.

### Definition of done (every task)

- Code + tests green in CI (`pytest`, `ruff`, `terraform validate`, web build).
- You ran it and verified real behavior yourself (row counts / live URL / logs).
- You can explain how it works and why it's built that way.
- PR reviewed and merged.
- **Operationalized, not just coded** — table materialized, endpoint live, data
  backfilled. Tasks that owe a real-data step carry a **▶ Run / backfill** line.

## Architecture decisions (locked)

| Decision | Choice |
|---|---|
| Infrastructure | GCP `data-architecture-498123`, us-west1: GCS bronze → BigQuery silver/gold, Cloud Run jobs + Cloud Function collectors, Terraform (2 roots), Cloud Scheduler |
| Transform engine | Python silver loaders (bronze→silver) + **dbt for silver→gold** (class requirement met; full dbt migration MIG-1/2/3 stays optional) |
| End product | FastAPI + React dashboard, one public Cloud Run service reading gold live |
| **Deep dive (new)** | **Option 2: Text-to-SQL agent** — `POST /ask` on the existing service, **Gemini 2.5 Flash via Vertex AI** (`location=global`, temperature 0, thinking off), layered guardrails, committed eval harness |
| **Synthetic data (new)** | Separate dataset **`event_demand_synth`**; hybrid = copy of the real star + researched **real** venue capacities + synthetic-only infill (sellout, resale); ~50x scale table for benchmarking; agent gets an opt-in synth toggle |
| **Benchmark (new)** | Primary: `trends_silver` full-bronze re-read → 14-day window (real incident, before/after runtime + bytes). Secondary: partitioned/clustered vs unpartitioned gold (bytes scanned + latency), run at 50x synth scale |
| Data validation | Great Expectations gates in the nightly job + offline pytest suite |
| Medallion | Bronze GCS JSON (`dt=` partitions) → silver per-source facts + conformed dims → gold star `fact_event_demand` + `forecast_event_price` |

**Locked schema:** silver constellation + gold star per `docs/data-model.md`. The
honest/observed-only rule stands: no forward-fill outside explicitly labeled tables.

## Why we can defend each choice (Q&A prep — keep this current)

- **Cloud Run (Python) + dbt, not Dataproc/Spark or Databricks** — our volume is
  ~46 GiB bronze JSON and ~1–2 M-row fact tables; BigQuery does the heavy SQL, jobs
  finish in minutes on 1 vCPU. Spark adds cluster cost/ops for zero benefit below
  ~100 GB working sets. **Rejected:** Dataproc (overkill, slower iteration),
  Databricks (cost, new platform for no gain at this scale).
- **dbt only for silver→gold (for now)** — the class requires dbt where it pays off:
  star assembly, tests, lineage. Bronze→silver stays Python because it parses raw
  JSON/HTML with per-source quirks (pytrends payloads, JSON-LD), which dbt can't do.
  **Rejected:** full dbt migration before Monday (MIG-1/2/3 — real but not urgent).
- **BigQuery, not Postgres/DuckDB** — serverless OLAP, per-query billing, native
  partitioning/clustering (our benchmark), IAM-integrated read-only agent access.
  **Rejected:** Postgres (row-store, we're analytical), DuckDB (no shared cloud state).
- **Gemini 2.5 Flash on Vertex AI, not Claude/GPT** — runs inside the same GCP
  project/billing (education credits), no new API key, ~$0.003/question, and
  text-to-SQL at our schema size is well within Flash capability. **Rejected:**
  Claude/OpenAI (better raw quality we don't need; external billing + key management),
  Gemini Pro (5–10x cost, latency; kept as env-var fallback).
- **Guardrails layered, not single** — prompt rules alone can't stop bad SQL;
  AST validation alone can't stop semantically wrong queries. Stack: domain-refusal
  prompt → sqlglot AST (SELECT-only + table allow-list) → BQ dry-run + bytes cap →
  LIMIT injection + timeout → **IAM read-only SA as the physical backstop** → rate
  limits + daily budget. Any single layer can fail safe.
- **Agent allow-list excludes `fact_event_demand_continuous` and `tm_events`** —
  the schema itself constrains the agent: continuous is team-derived fill (would
  poison coverage answers), `tm_events` carries prices forward (forward-fill trap).
  This is the deep-dive's "schema enabled/constrained the agent" story.
- **Synthetic data in a separate dataset, never in honest tables** — real venue
  capacity is *researched* (curated CSV with sources) because it's knowable;
  sellout/resale are *synthetic* (TM bronze has **0.0%** resale priceRanges — measured)
  and every synth table carries `synth_run_id` + `generator_version`. Same
  honesty ethos that split `fact_event_demand` from `_continuous`.
- **TM 2×/day, Trends single-stream 20s/800-day, RA 1 req/day** — collection caps are
  deliberate (quota math + a written RA agreement), documented in
  `docs/collection_efficiency_review.md` D1–D8.
- **Anchor+drift forecast, not ML-heavy** — 96% of shows never move price; anchor on
  last real price + tier drift cut premium-tier MAE $98→$5. **Rejected:** deep
  models (no signal to learn at our price-movement base rate).

## Where we are today (2026-08-08, post-recovery — details in `docs/REPO_STATE.md`)

| Layer | Ticketmaster | Google Trends | YouTube | Scene (19hz/RA/ticketpages) |
|---|---|---|---|---|
| Bronze | ✅ never stopped | ✅ never stopped | ✅ never stopped | ✅ re-live 08-08 (month lost) |
| Silver | ✅ current | ✅ backfilled 08-08 | ✅ backfilled 08-08 | ✅ loading 08-08 |
| Gold + forecast | 🔄 full-refresh in progress; nightly verified by 16:30 PT run | | | n/a (not in gold yet) |

> The July outage (36 failed nightly runs, unnoticed for a month) is written up in the
> REPO_STATE incident log — it's also our best "lesson learned" material for the blog:
> *freshness ≠ health*, and the alert only watched the pipelines that didn't break.

## Class requirements → evidence map

| Requirement | Status | Evidence / gap |
|---|---|---|
| Multi-layer cloud storage (bronze/silver/gold) | ✅ | GCS + BigQuery, `docs/data-model.md` |
| Pipeline runs on cloud, compute justified | ✅ | Cloud Run jobs; justification in Q&A section above |
| dbt for silver→gold | ✅ | `dbt/models/gold/*` incremental star + tests |
| End product, non-technical consumer | ✅ | live dashboard (Cloud Run) |
| Performance benchmark, before/after | 🔨 | BENCH-1/2 below |
| Continued GitHub use, public repo | ✅ | public; keep PR flow |
| Deep dive: text-to-SQL + guardrails + eval + reflection | 🔨 | AGENT-1..6 below |

## Task board

Format:

```
- [ ] **ID · Title**  ·  Owner: `____`
   - Prereqs: <task IDs, or "none — ready">
   - Build: <what to build>
   - Tests / done-when: <expectations>
```

### REPAIR — recovery from the July outage (done 2026-08-08, verify overnight)

- [x] **R1 · Deploy PRs #55–#58 to gold-refresh** · Owner: `TK`
   - Built: image `git-ab65e00` (windowed trends_silver, dims MERGE, bool-test fix,
     gold rewire), job updated, task-timeout 3600→5400s.
   - ▶ Verified: job describe shows new image; **final proof = tonight's 16:30 PT
     scheduled execution SUCCEEDED** (check execution status, not freshness).
- [x] **R2 · Backfill 28-day silver gap from banked bronze** · Owner: `TK`
   - ▶ Verified: `fact_trends_daily` +149,134 rows → 2026-08-08; `fact_youtube`
     +20,744 rows → 2026-08-08; `fact_trends` backfill running; dims re-merged
     (3,875 venues / 13,071 artists / 53,861 events, accumulating).
- [x] **R3 · Scene collection live** · Owner: `TK`
   - Built: terraform apply (2 jobs, 2 schedulers, scene SA, project-wide job-failure
     alert); smoke execution landed `nineteenhz/dt=2026-08-08` + `ticketpages/dt=2026-08-08`.
   - ▶ Verified: RA fires on schedule 08:15 PT Sat (1 req/day agreement — never manual-run same day).
- [x] **R4 · Gold full-refresh + forecast re-export + GX gate** · Owner: `TK`
   - ▶ Verified 08-08 (NN): freshness SQL — all five facts at 2026-08-08; 13,956
     shows serve non-null `forecast_price` via `/shows`.
   - Prereqs: R2 trends backfill finishing.
   - Build: `dbt build --full-refresh -s fact_event_demand fact_event_demand_continuous`,
     then `pipeline/gold/export_predictions_table.py`, then GX checkpoints.
   - Done-when: freshness SQL shows every fact ≥ 2026-08-08; `/show/<hero>` returns
     non-null `forecast_price`; incident closed in REPO_STATE.

### AGENT — Text-to-SQL deep dive (branch `tk/text2sql-agent`) · ⭐ HIGH

- [x] **AGENT-1 · Schema-context generator** · Owner: `TK` (PR #61)
   - Prereqs: none — ready (Vertex API enabled, `aiplatform.user` granted, model
     verified: `gemini-2.5-flash` @ `location=global` needs `thinking_budget=0`).
   - Build: `eda/build_schema_context.py` → committed `api/schema_context.md` from
     `INFORMATION_SCHEMA` + curated semantic notes (per-pull-normalized `interest`;
     `show_date` vs `snapshot_date`; honest vs continuous; headliner-only gold;
     name-hash `artist_id`, `bare_dma()` joins) + dry-run-validated few-shots.
     Hard-fail >16k chars.
   - Tests / done-when: generator re-runs deterministically; context file committed.
- [x] **AGENT-2 · `api/text2sql.py` + `POST /ask`** · Owner: `TK` (PR #62)
   - Prereqs: AGENT-1.
   - Build: injectable `LlmClient`/`QueryRunner` (mirror `api/gold.py` seam);
     guardrail pipeline (prompt refusal → sqlglot AST SELECT-only + allow-list →
     dry-run + 512MB block → bytes-billed 1GB + LIMIT 200 + 20s → per-IP 6/min +
     global 300/day); one self-repair retry; always-200 response with
     `status/sql/rows/answer/guardrails[]/bytes/latency`.
   - Tests / done-when: `tests/test_text2sql.py` offline (FakeLlm/FakeRunner): DROP
     blocked, allow-list, CTE aliases, LIMIT injection, refusal, retry, `/ask` contract.
- [x] **AGENT-3 · Eval set + harness + committed report** · Owner: `TK` (PR #64)
   - ▶ Report committed at **92%** (NN, PR #89), then the set was EXPANDED 26→31
     questions with five live-user-failure probes (invented genre/metro literals)
     and re-run after the canonical-vocab + event_id context fixes: **100% over 93 runs**
     (easy 100 · join 93 · aggregate 86 · trick 100) — PRs #92/#98, 2026-08-09.
   - Prereqs: AGENT-2.
   - Build: `eda/text2sql_eval_set.yaml` (~24 q: easy/join/aggregate/trick — tricks
     probe the semantic landmines incl. expected refusals);
     `eda/eval_text_to_sql.py --runs 3`, execution-result match scoring →
     `eda/output/text_to_sql_eval.md` (accuracy by tier + failure taxonomy).
   - Tests / done-when: scoring logic offline-tested; report committed; failure
     section written (rubric item d).
- [x] **AGENT-4 · AskPanel UI** · Owner: `TK` (PR #65)
   - Prereqs: AGENT-2.
   - Build: `web/src/components/AskPanel.tsx` (question box, example chips, guardrail
     badges, SQL block prominent, results table, answer, bytes/model/latency footer)
     + header toggle + `askQuestion()` client + vitest.
- [x] **AGENT-5 · Deploy + demo hardening** · Owner: `NN`
   - ▶ 08-08 (NN): service live on image `git-5f71ebb` with
     `TEXT2SQL_MODEL`/`VERTEX_LOCATION`, `min-instances 1`, `max-instances 2`,
     startup-cpu-boost. Live smoke passed. ▶ 08-09 (TK): redeployed `git-97f2e03`
     (multi-turn ask + feedback + docs page + fresh heroes + vocab context).
   - Budget alert: waived by TK 2026-08-09 ("don't worry about it") — rate caps +
     bytes-billed limits remain the cost guard.
   - Prereqs: AGENT-2 (AGENT-4 ideally), R4 (fresh hero shows).
   - Build: rebuild service image (includes regenerated `heroShows.ts`), deploy with
     `TEXT2SQL_MODEL`/`VERTEX_LOCATION`, `--max-instances 2`, `--min-instances 1`
     **through Monday** (kills the measured 152s cold start; revert after), $10
     billing budget alert.
   - ▶ Run: live smoke — easy question, blocked `DROP`, off-domain refusal.
- [x] **AGENT-6 · Synth-mode toggle** · Owner: `TK` (PR #85)
   - Prereqs: AGENT-2, SYNTH-3.
   - Build: request flag `dataset: real|synth` switching the allow-list + context to
     `event_demand_synth` tables; every synth answer labeled synthetic in the UI.
   - Done-when: same question runs against both datasets; graded eval stays real-only.

### SYNTH — synthetic demand layer (`event_demand_synth`)

Heuristic model to encode (from TK's design session — review before building):
**demand pressure ≈ f(artist local popularity [fact_trends_daily], artist global
popularity [yt_subscribers], venue capacity, event type)** → sellout probability +
sellout speed → resale multiplier. Undersold big rooms resell **below** face
(Chris Lake @ small club: $100 → $50–60 day-of); oversubscribed small rooms sell out
fast and resell **above** face (MGMT @ Public Works); festival day tickets anchor to
the **sum of headliners' typical solo prices** (Outside Lands: $250 face → $500 resale).

- [x] **SYNTH-1 · Real venue capacities (curated, NOT synthetic)** · Owner: `TK` (PR #81)
   - Prereqs: none — ready. `dim_venue.capacity` exists and is **0% filled**.
   - Build: researched capacities w/ source URLs for all Bay Area venues in
     `fact_nineteenhz` + top ~150 venues by priced-event count →
     `data/reference/venue_capacities.csv` (committed) + idempotent loader filling
     `dim_venue.capacity`; remaining venues get **tier estimates only in the synth
     dataset**, labeled `capacity_source='tier_estimate'`.
- [x] **SYNTH-2 · Seeded generator** · Owner: `TK` (PR #82)
   - Prereqs: SYNTH-1.
   - Build: `synth/heuristics.py` (pure functions, offline-tested) +
     `synth/generate.py` (numpy seeded RNG, committed config; same seed → identical
     output); copies the real star into `event_demand_synth` and infills:
     `synth_event_demand` (sellout_date, sold_out flag, resale_price_min/max series,
     demand_score), calibrated from real price/genre/DMA distributions.
- [x] **SYNTH-3 · Load + QC** · Owner: `TK` (PR #83, `eda/output/synth_review.md`)
   - Prereqs: SYNTH-2.
   - Build: BQ load with provenance columns; `eda/synth_review.py` — real-vs-synth
     distribution comparison (deterministic md + plots).
   - ▶ Run: generate twice, diff outputs byte-identical; verify zero writes outside
     `event_demand_synth`.
- [x] **SYNTH-4 · 50x scale table for benchmarking** · Owner: `TK` (used by BENCH-2)
   - Prereqs: SYNTH-2.
   - Build: ~50x replication with jittered keys/dates → few-GB
     `event_demand_synth.fact_event_demand_50x` (+ unpartitioned twin for BENCH-2).

### BENCH — performance benchmark (before/after numbers for slide + blog)

- [x] **BENCH-1 · trends_silver windowing (primary)** · Owner: `TK` (PR #66, `eda/output/benchmark_trends_window.md`)
   - Prereqs: R1 verified (need one post-fix nightly run).
   - Build: `eda/benchmark_trends_window.py` — before: incident evidence (47 min
     2026-07-08, 52→59 min growth, then 28 consecutive 3600s timeouts, from logs);
     after: post-deploy step duration + objects/MiB read → committed md + chart.
- [x] **BENCH-2 · Partitioning/clustering at 50x scale (secondary)** · Owner: `TK` (PR #84, `eda/output/benchmark_partitioning.md`)
   - Prereqs: SYNTH-4.
   - Build: `eda/benchmark_partitioning.py` — fixed query suite (incl. real agent-
     generated queries) against partitioned vs unpartitioned 50x tables; dry-run
     bytes + timed latency → md + chart. Create → verify → delete the copies.

### DOCS — public-repo readiness

- [x] **DOCS-1 · REPO_STATE incident entry + status refresh** · Owner: `TK`
- [x] **DOCS-2 · Root README refresh** · Owner: `TK` (PR #88)
   - Stale: 683-only framing, missing scene sources/web/continuous table, "every 4h"
     TM cadence, CI web job, cost section. Reframe for the lakehouse class.
- [x] **DOCS-3 · Dir READMEs** · Owner: `TK` (PR #88)
   - Short runbook-style for `pipeline/`, `model/`, `common/`, `terraform/`,
     `terraform/gtrends/`, `web/`, `eda/`, `docs/`; fix stale `dbt/README.md`.
- [x] **DOCS-4 · data-model.md additions** · Owner: `TK` (PR #86)
   - Scene facts columns, `tm_observations`/`tm_events`/`fact_trends_daily`/
     `forecast_event_price` tables, join-key normalization notes (currently only in
     gitignored CLAUDE.md), `event_demand_synth` section with provenance rules.

### DEMO — Monday presentation support (slides = TK/team, not agents)

- [x] **DEMO-1 · Demo runbook** · Owner: `TK` (PR #87, `docs/demo-runbook.md`)
   - Pre-warm checks, click path (dashboard hero → AskPanel: good question → blocked
     DROP → semantic refusal → eval table), Swagger fallback, revert list
     (min-instances → 0 after Monday).
- [ ] **DEMO-2 · Dry run Sunday evening** · Owner: `____`
   - Full 7-min walkthrough against the live URL, timed.

### BLOG — after Monday

- [ ] **BLOG-1 · Draft** — sections map to existing docs: transformations_showcase →
  architecture; eval report → deep dive; benchmark mds → benchmark; incident log →
  reflection (plus presentation feedback).
- [ ] **BLOG-2 · Benchmark + eval charts** — reuse committed eda outputs.
- [ ] **BLOG-3 · Repo final pass** — READMEs done, hero screenshots, banner image.

## Dependency quick-reference (what's unblocked)

> **Frontier (Sat 08-09, post PRs #92–#98):** DEMO-2 dry run · BENCH-2 re-run
> against fresh gold + twin cleanup · Sat scheduled-fire checks (scene 08:00/
> 08:15 PT, gold-refresh 16:30 PT) · then BLOG. Everything else on this board is
> merged, deployed (`git-97f2e03`), and verified — incl. the 08-09 user-feedback
> batch: multi-turn ask + 👍/👎 feedback (`event_demand_ops`), canonical-vocab
> fix for the two live-failed questions (eval 100%/31q), fill toggle, search
> click-through, "How it works" docs page, fresh heroes.

- After AGENT-2: AGENT-3 (eval), AGENT-4 (UI) in parallel.
- After SYNTH-2: SYNTH-3 + SYNTH-4 → BENCH-2.
- After R1's overnight verification: BENCH-1.
- AGENT-6 last (needs both agent + synth).
- Shared hotspots: `api/` (AGENT-2/4/5), `dim_venue` (SYNTH-1 vs nightly dims MERGE).
