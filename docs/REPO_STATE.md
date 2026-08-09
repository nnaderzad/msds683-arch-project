# Repo state — read this first

> **⚠️ Maintenance rule (agents and humans):** update this file as part of **every
> commit / PR** that changes pipeline behavior, deploys anything, moves data
> coverage, or changes project status. Stale entries are worse than none — fix or
> delete what you can't verify. Refresh the "Last verified" stamps when you
> re-check a section.

**Last full review:** 2026-08-08 (post-pause recovery: month-long gold-refresh
outage repaired — see incident log; lakehouse-class sprint kicked off — plan in
[`lakehouse-plan.md`](lakehouse-plan.md))

## What this is

Event-demand forecasting data architecture for Bay Area electronic-music events
(MSDS 683). Medallion lake on GCP + BigQuery star schema + anchor-and-drift price
forecaster + public demo dashboard. Deep dives:

- Architecture narrative: [`../README.md`](../README.md)
- Schema (silver constellation + gold star): [`data-model.md`](data-model.md)
- Stage-by-stage pipeline walkthrough: [`transformations_showcase.md`](transformations_showcase.md)
- Collection-efficiency decision record (2026-07): [`collection_efficiency_review.md`](collection_efficiency_review.md)
- Data review (raw samples, event trace, coverage, 19hz/RA findings):
  [`../eda/data_review_2026-07.md`](../eda/data_review_2026-07.md)
  (regenerate the numbers with `python eda/data_review.py`)

## Live system (GCP project `data-architecture-498123`, us-west1)

**Billing:** `BillingAcctForEdu_MSDS692` (`01EB77-4F3D56-814EA1`), linked 2026-07-04
after the previous account closed (see incident log).

| Component | What | Schedule (PT) | Deployed via |
|---|---|---|---|
| `ticketmaster-daily-extract` (Cloud Function gen2) | nationwide Discovery sweep → bronze + `tm_events` + `tm_observations` | 05:00, 15:00 (D8, live since 2026-07-08) | `terraform/` (state local, on Niki's machine) or `cloud_functions/ticketmaster_daily/deploy.sh` |
| `gtrends-daily` (Cloud Run job) | Trends national + DMA-snapshot + tier-1 per-DMA daily units → bronze + silver | 11:00 (D8) | `terraform/gtrends/` (remote state, anyone can apply) |
| `gtrends-backfill` (Cloud Run job) | deep per-DMA daily series, on demand | manual | `terraform/gtrends/` |
| `youtube-daily` (Cloud Run job) | channel stats + topic views → bronze + `fact_youtube` | 15:00 (D8) | `terraform/gtrends/` |
| `gold-refresh` (Cloud Run job) | silver loaders (windowed reads, image `git-ab65e00`, task-timeout 5400s) → dbt build → forecast → GX gate | 16:30 (D8) — **broken 06-30→08-08, redeployed 2026-08-08**, see incident log | `terraform/` (image via gcloud, 08-08) |
| `nineteenhz-daily` + `ra-daily` (Cloud Run jobs) | scene listings → bronze (`nineteenhz/`, `ticketpages/`, `ra/`) | 08:00 / 08:15 — **live since 2026-08-08** (terraform apply + smoke run; the 07-08→08-07 listings gap is permanent) | `terraform/gtrends/scene.tf` |
| `event-demand-api` (Cloud Run service) | FastAPI + React demo + **`POST /ask` text-to-SQL agent (Gemini via Vertex; multi-turn follow-ups, 👍/👎 feedback, canonical-vocab context, answer rows link into the dashboard)** + `/search` browse + **"How it works" docs page** (renders the committed md bundled per deploy) + filled-vs-observed price toggle, reads gold live | min-instances 1 through Mon 08-11, then revert to 0 (cold start **~30 s** since 08-09 — was 152 s; see incident log) | gcloud only (not yet in terraform); image `git-87afd36` (08-09). SA roles: BQ jobUser + dataset dataViewer + `aiplatform.user` + **`readSessionUser`** (Storage reads) + dataEditor on `event_demand_ops` only |

**Ops telemetry:** `/ask` answers carry thumbs-up/down buttons; votes stream into
**`event_demand_ops.ask_feedback`** (separate dataset — the service SA has
`dataEditor` there ONLY, keeping the analytical dataset read-only for the agent).
Created live via bq CLI 2026-08-09; `terraform/ops.tf` holds the IaC with
**`terraform import` commands the main-root state holder must run before their
next apply** (state lives on Niki's machine). Mine thumbs-down rows into the
eval set — that's the feedback loop's purpose (offline curation, never online
learning).

Data lands in `gs://data-architecture-498123-{raw,processed,analytics}` and
BigQuery dataset `event_demand_analytics`.

**Scene sources (first bronze landings 2026-07-08):** `nineteenhz/` (Bay Area
listing HTML — 456 events, 74.6% priced), `ra/` (GraphQL JSON, area 218 — 100
events/day at the agreed 1 request/day, incl. per-event `attending`),
`ticketpages/` (JSON-LD offers from eventbrite/shotgun — availability incl.
SoldOut). Daily scheduling: PR #54 (jobs above). Silver: `fact_nineteenhz` /
`fact_ra` / `fact_ticketpages` created + loaded 2026-07-08 via
`pipeline/silver/scene_to_silver.py`; nightly refresh via the gold-refresh
`scene_silver` step lands with PR #55. Manual runs:
`nineteenhz_api/collect_19hz.py`, `ra_api/collect_ra.py`,
`nineteenhz_api/poll_ticket_pages.py` (each with `--land-raw`). First-pull
findings + next steps: `eda/data_review_2026-07.md`.

## Clock & cadence (D8, 2026-07)

**PT (America/Los_Angeles) is the project's reference timezone.** Every
human-facing time in docs, dashboards, and scheduler configs carries an explicit
tz label; all Cloud Scheduler crons are defined in `America/Los_Angeles`.
Storage partitions (`dt=`) and `snapshot_date` remain **UTC days** — the full
"SF-day" migration is heavy and deferred (backlog, team-plan.md). Instead, the
cadence keeps every capture inside **one UTC day**: PT 01:00–15:59 maps into the
same UTC day year-round, so 15:00 PT is the latest safe collection slot. That is
what makes gold's `snapshot_date` joins apples-to-apples — all sources' rows for
a given key come from the same collection cycle.

Target daily cycle (serve-by 19:00 PT — users decide where to go out ~7 PM):

| PT time | Job | Why here |
|---|---|---|
| 05:00 | TM sweep #1 | insurance + overnight announcements |
| 11:00 | gtrends-daily | single-stream ~3–4 h crawl (worst 5h40m ends 16:40); fetch hour doesn't change Trends content — the freshest reliable point is always yesterday's |
| 15:00 | TM sweep #2 | load-bearing capture; latest one-UTC-day slot |
| 15:00 | youtube-daily | ~30 min; freshest same-day stats |
| 16:30 | gold-refresh | builds from SAME-DAY TM + YouTube + latest Trends; live ~17:15 PT |

Known edge: a worst-case Trends run (deadline 5h40m) ends 16:40 PT, so that
day's gold reads a partially-loaded Trends day — acceptable; loaders read
whatever bronze has landed and the next refresh completes it.

**TM sweep completeness gate:** the function writes its run summary to stderr
(→ ERROR severity → the Cloud Monitoring email alert) whenever any state failed,
any state was skipped on call budget, or a silver/observations merge failed;
gold still builds. Covered by `tests/test_ticketmaster_daily.py`.

## Data freshness

Re-check with (also in `eda/collection_sizing.py --freshness`):

```bash
bq query --use_legacy_sql=false '
SELECT "tm_observations" src, CAST(MAX(snapshot_date) AS STRING) latest FROM `data-architecture-498123.event_demand_analytics.tm_observations`
UNION ALL SELECT "fact_trends", CAST(MAX(snapshot_date) AS STRING) FROM `data-architecture-498123.event_demand_analytics.fact_trends`
UNION ALL SELECT "fact_trends_daily", CAST(MAX(snapshot_date) AS STRING) FROM `data-architecture-498123.event_demand_analytics.fact_trends_daily`
UNION ALL SELECT "fact_youtube", CAST(MAX(snapshot_date) AS STRING) FROM `data-architecture-498123.event_demand_analytics.fact_youtube`
UNION ALL SELECT "fact_event_demand", CAST(MAX(snapshot_date) AS STRING) FROM `data-architecture-498123.event_demand_analytics.fact_event_demand`
ORDER BY src'
```

As of 2026-08-08 (post-pause recovery day — see incident log):

| Table | Latest snapshot | Note |
|---|---|---|
| `tm_observations` | 2026-08-08 | collector never stopped through the outage |
| `fact_trends` | 2026-08-08 | current via the nightly 14-day window; **Jul 12–24 hole FILLED 2026-08-09** (310,800 rows / 13 days merged + verified — the third attempt, succeeding thanks to PR #63's gsutil retry). History is now gap-free |
| `fact_trends_daily` | 2026-08-08 | 28-day backfill 08-08 (+149,134 rows) |
| `fact_youtube` | 2026-08-08 | 28-day backfill 08-08 (+20,744 rows) |
| `fact_event_demand` | 2026-08-08 | full-refresh 08-08 (also backfills `local_interest` history per the PR #58 rewire) |
| `forecast_event_price` | re-exported 08-08 | was frozen at 06-30 for 39 days |
| `fact_nineteenhz` / `fact_ra` / `fact_ticketpages` | 2026-08-08 | scene jobs live 08-08; the 07-08→08-07 gap is permanently lost (point-in-time listings) |

`dim_venue.capacity` is now populated for 179 venues from the curated
`reference/venue_capacities.csv` (312 venues researched with sources on
2026-08-08, 197 with capacities; the remainder are scene-only venues not in
`dim_venue`).

## Known bottlenecks (measured)

- **TM pricing = 22.7% of observations / ~23% of events** — structural: `priceRanges`
  only populates for TM-host-fulfilled primary inventory; club shows
  (TicketWeb/venue systems) never get it; 8,868/8,870 ever-priced events were priced
  from their first observation (re-polling unpriced events is pointless).
  Official fix path: Inventory Status API access (requested — see decision record).
- **Headliner resolution = 42.9% of priced events** — `attraction_names` missing at
  the source for most of the rest (`eda/output/headliner_gap_diagnosis.md`); caps
  every Trends/YouTube join. 498 safe title-match recoveries identified.
- **Google Trends throttle** — unofficial endpoint, deterministic 20s/call single
  stream, 800 calls/day budget. pytrends is archived (Apr 2025) — pinned 4.9.2
  still works; migration fallback: pytrends-modern or a paid widget API.

## Active work / branch map

- `main` — state of record. **2026-08-09 overnight sprint** (user-feedback driven,
  all merged + deployed as image `git-87afd36`): PR #92 agent follow-up history +
  zero-row corrective retry, #93 `/show` gap-filled price series
  (`history_filled` from the continuous table), #94 `POST /ask_feedback` →
  `event_demand_ops` (👍/👎), #95 committed-docs endpoints + Docker bundle
  (+ `.gcloudignore`/`.dockerignore` hygiene: personal notes were reaching build
  contexts), #96 the web batch (price formatting, legend rebuild, clickable
  search rows, fill toggle, embedded + multi-turn ask, feedback buttons, "How it
  works" docs view), #97 hero regeneration (12 heroes, 35–46 signal days each),
  #98 **canonical vocabularies** — fixes two live user questions the agent
  answered flat-wrong by inventing genre/metro literals; **eval 95% over 93 runs
  on the expanded 31-question set** (was 92%/26).
- 2026-08-08 lakehouse sprint (all merged same-day):
  PR #59 repo sync (plan docs → `docs/`, gitignore instructor notes), #60
  **`docs/lakehouse-plan.md`** (the team plan — task board + Q&A prep), #61
  text-to-SQL schema context (`api/schema_context.md`, generated + committed),
  #62 guardrailed `POST /ask` service (Gemini 2.5 Flash on Vertex), #63 trends
  loader gsutil retry, #64 eval set + harness (25 questions, execution-match),
  #65 AskPanel UI, #66 windowing benchmark (banked pre-fix log evidence), #67
  `/search` + `/genres` + dashboard search panel, #81 synth demand heuristics +
  **197 researched venue capacities** filling `dim_venue.capacity`.
- July-era PRs #54–#58 (scene jobs/silver, dims MERGE, bool-test hotfix, gold
  rewire) were merged 2026-08-08 after sitting un-deployed for a month — the
  outage in the incident log.
- Open work: GitHub issues AGENT-5/6, SYNTH-2..4, BENCH-2, DOCS-2..4,
  DEMO-1/2, BLOG-1 (created from the plan's task board).
- Older `tk/*`, `niki/*`, `noam/*` branches are merged history (PRs #17–#53).

### Pending deploys / user actions (2026-08-08)

The July backlog (PRs #54–#58 deploys) was **fully executed 2026-08-08**:

- [x] gold-refresh image `git-ab65e00` built + job updated (task-timeout
  3600→5400s headroom); covers the #55 windowing, #56 dims-MERGE, #57 bool-test
  hotfix, #58 gold rewire.
- [x] Shared ingestion image rebuilt; `terraform -chdir=terraform/gtrends apply`
  (nineteenhz-daily + ra-daily jobs, 2 schedulers, scene SA, **project-wide
  job-failure alert**); `nineteenhz-daily` smoke-executed — fresh bronze landed.
- [x] 28-day silver backfill from banked bronze (trends, trends_daily, youtube);
  dims rebuilt (accumulate-MERGE + capacity fill).
- [x] One-time `dbt build --full-refresh` (both gold models) + forecast
  re-export + GX gate.
- [x] Vertex AI enabled; `event-demand-api` SA granted `roles/aiplatform.user`.

Done Friday evening (verification + follow-through):

- [x] **Nightly verified GREEN**: execution `gold-refresh-9mlf8` (Fri 16:30 PT
  schedule) SUCCEEDED in 41 min — first success since 2026-06-30. `trends_silver`
  windowed: **33.1 min** (vs 52–60 min + 27 timeouts pre-fix; still serial
  per-file gsutil reads — batching is the next optimization, good blog material).
  Forecast re-exported same run (17:11 PT); 13,956 shows serve non-null
  `forecast_price` (verified NN).
- [x] Demo service deployed (rev `git-5f71ebb`): `/ask` agent + `/search` +
  synthetic-sandbox toggle live, `--min-instances 1` (0.15 s loads),
  `TEXT2SQL_MODEL`/`VERTEX_LOCATION` env, $10 budget alert.
- [x] Final eval committed (NN, PR #89): **92% overall** across 26 questions × 3
  runs — easy 100%, join 86%, aggregate 83%, trick refusals 100%. Context fixes
  for the four failure modes followed in PR #90.
- [x] Post-fix benchmark capture (NN, PR #89) + terraform synced to deployed
  reality.

Done overnight Sat 2026-08-09 (see the 08-09 sprint in the branch map):

- [x] `fact_trends` Jul 12–24 hole **filled + verified** (310,800 rows, 13 days).
- [x] Heroes regenerated (12 credible, 35–46 signal days each) — shipped in the
  08-09 image.
- [x] Eval re-run after the vocab/context fixes: **95%** (93 runs, 31 questions —
  set expanded with the five live-failure probes).
- [x] Service rebuilt + redeployed (`git-87afd36`): multi-turn ask, feedback
  buttons, fill toggle, search click-through, docs page, fresh heroes.

Still open (weekend handoff):

- [ ] Sat: confirm the scene jobs' **first scheduled fires** (08:00/08:15 PT) land
  `dt=2026-08-09` bronze, and Sat's 16:30 PT gold-refresh stays green.
- [ ] Sat/Sun: `eda/benchmark_partitioning.py --setup --run` re-run
  (un-degenerates the 14-day-window query against fresh gold), then `--cleanup`
  the 3.98 GiB twins after the blog numbers are final; timed demo dry run
  (`docs/demo-runbook.md`).
- [ ] Niki: `terraform import` the three `ops.tf` resources before the next
  main-root apply (commands in the file header).
- [ ] Mon after the demo: `--min-instances 0` revert (runbook's revert list).
- [ ] Carried over: official Trends API alpha application (optional);
  `docs/tm_access_request.md` still unanswered.

## Incident log

- **2026-08-09: service deploy failed on the startup probe (no user impact).**
  Revision `00011` never became healthy: the FastAPI lifespan **blocked the
  port** on the full gold pre-warm, which ran through BigQuery's paginated REST
  path (the Storage client was never installed — the "152 s cold start" was
  mostly REST pagination), and adding the continuous-price frame pushed it past
  Cloud Run's 4-minute probe. Traffic stayed on the old healthy revision
  throughout. Fix (PR #99): pre-warm in a daemon thread (port binds in seconds;
  requests mid-warm block on a thread-safe lazy-load lock) +
  `google-cloud-bigquery-storage` (Arrow streaming). Follow-on: the Storage path
  needs `bigquery.readsessions.create` → granted **`roles/bigquery.readSessionUser`**
  to the service SA (read-only; the never-writes invariant holds). Full gold
  load measured at **30 s** post-fix. Lesson: a "startup pre-warm" that blocks
  serving is a probe timeout waiting for its tables to grow.
- **2026-08-09 ops note:** this machine's `gcloud` default project changed
  externally mid-session (to an unrelated project) — one deploy failed with
  SERVICE_DISABLED against the wrong project. All deploy/log commands should
  pass `--project data-architecture-498123` explicitly.
- **2026-07-05 → 2026-08-08: month-long gold-refresh outage (36 consecutive
  failed nightly runs), unnoticed for four weeks.** The team paused after
  2026-07-08 with PRs #54–#58 merged-ready but **never merged or deployed**; the
  fixes they contained were exactly what the nightly job needed. Two failure
  phases: (1) 07-05→07-11 — the known dbt `BOOL IN ('True','False')` abort
  (PR #57's fix) killed every run at `dbt_build`; facts still advanced (dbt
  materializes before testing) but `forecast_export` never ran, freezing
  `forecast_event_price` at 06-30. (2) 07-12→08-07 — the un-windowed
  `trends_silver` step (PR #55's fix) grew past the 3600 s task timeout
  (51.7 → 59.0 min on 07-09/07-10, then 27 consecutive nightly timeouts,
  banked in `eda/output/benchmark_trends_window_runs.csv`), so runs died
  ~35 min before dbt was ever invoked. Silver/gold facts froze at 07-11 —
  the last execution to clear `trends_silver`. **Why nobody noticed:** the only
  alert policies watched the two collectors that stayed healthy; there was no
  alert on gold-refresh, the demo kept serving 200s with silently-null
  forecasts, and `gcloud run jobs list` shows a green ✔ for a job whose every
  execution fails (it reflects the job resource, not executions). **What was
  lost:** one month of scene listings (19hz/RA jobs from PR #54 never existed —
  point-in-time, unrecoverable) and one month of forecast history. **What was
  NOT lost:** TM/Trends/YouTube collectors ran perfectly all month, so 28 days
  of bronze sat banked and the whole silver/gold gap was rebuilt from it on
  08-08 (backfills + `dbt build --full-refresh` + forecast re-export). Repairs
  deployed 08-08: image `git-ab65e00` (windowed reads + bool-test fix +
  accumulate-dims + gold rewire), scene jobs + schedulers, and PR #56's
  **project-wide failed-execution alert** — the structural fix for the
  four-week blind spot. Lessons: *merged ≠ deployed* (July's lesson, compounded:
  this time the PRs weren't even merged); an alert that doesn't cover the thing
  that breaks is indistinguishable from no alert; freshness ≠ health, again.
  **CLOSED 2026-08-08 17:11 PT:** the same-day scheduled run (`gold-refresh-9mlf8`)
  succeeded end-to-end in 41 min — windowed `trends_silver` 33.1 min, dbt green,
  forecast re-exported, GX gate passed.
- **2026-07-05 → 07-08: gold-refresh aborts at dbt tests;
  `forecast_event_price` stale since 06-30.** Every scheduled run since 07-05
  failed `relationships(fact_event_demand.artist_id → dim_artist)` and
  `(venue_id → dim_venue)` (24 orphan rows) and exited before `forecast_export`
  — fact tables still advance (dbt materializes before testing), so freshness
  checks alone missed it. Root cause: `fact_event_demand` is incremental
  (point-in-time history) while dims are `WRITE_TRUNCATE` rebuilds from
  *current* `tm_events` — when an event changes venue/headliner, its old fact
  rows reference dim members that vanish from the rebuild (e.g.
  `rZ7HnEZ1AfZbrf` moved venues; its June rows orphaned). The `event_id`
  relationship already had `severity: warn` for exactly this drift; artist/venue
  didn't. **Fix #1 (PR #53, deployed 07-08 as image `git-82cafa2`):** same
  severity treatment — verification run confirmed both tests now WARN (44/48
  orphans, drift keeps growing until PR #56's accumulating dims land). **The
  same verification run then exposed fix #2 (PR #57):** the
  `accepted_values` test on `fact_event_demand_continuous.price_is_filled`
  renders as `BOOL IN ('True','False')` under the rebuilt image's dbt version →
  BigQuery Database Error → build aborts *regardless of the test's warn
  severity*; `quote: false` fixes it (verified live, 6/6 tests pass). Also
  timed on that run: `trends_silver` re-downloads the whole growing ibr bronze
  (47 min vs the job's 3600s timeout) — windowed in PR #55. Structural
  follow-ups shipped in PR #56: accumulating dims + a project-wide alert on
  failed job executions. Lesson: **freshness ≠ health — check execution
  status, not just `MAX(snapshot_date)`** (the old email alert fires only on
  the two gtrends jobs' ERROR logs).
- **2026-07-01 → 07-04: billing outage.** `BillingAcctForEdu_MSDS691` closed →
  `billingEnabled: false` → all schedulers/jobs halted after 2026-06-30 runs.
  Fixed 07-04 by linking the MSDS692 account. Losses: Trends DMA snapshots +
  YouTube snapshots for Jul 1–3 (point-in-time, unrecoverable); TM raw landed
  through Jul 1 07:07 UTC, so only Jul 2–3 sweeps lost. Schedulers did NOT
  auto-fire after relink on Jul 4 — jobs needed manual kicks.
- **2026-06-18 → 07-04: `tm_observations` deploy-gap freeze.** Commit `410fca8`
  (Jun 28) added `append_observations` to the TM cloud function, but the deployed
  build remained Jun 11 — observations silently froze at Jun 18 (raw kept landing).
  Recovery: redeploy function + bronze backfill Jun 19→Jul 1 (idempotent loader).
  Lesson: after merging collector changes, verify the *deployed* revision, not
  just the merge — and check `MAX(snapshot_date)` the next day.
