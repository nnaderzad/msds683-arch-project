# Repo state — read this first

> **⚠️ Maintenance rule (agents and humans):** update this file as part of **every
> commit / PR** that changes pipeline behavior, deploys anything, moves data
> coverage, or changes project status. Stale entries are worse than none — fix or
> delete what you can't verify. Refresh the "Last verified" stamps when you
> re-check a section.

**Last full review:** 2026-07-08 (collection cadence D8; recovery from the 07-01
billing outage verified complete)

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
| `gold-refresh` (Cloud Run job) | silver loaders (incl. `fact_trends_daily`, image `git-82cafa2`) → dbt build → forecast → GX gate | 16:30 (D8) — 07-05..08 failures fixed, see incident log | `terraform/` |
| `nineteenhz-daily` + `ra-daily` (Cloud Run jobs) | scene listings → bronze (`nineteenhz/`, `ticketpages/`, `ra/`) | 08:00 / 08:15 (PR #54 — **pending merge + deploy**) | `terraform/gtrends/scene.tf` |
| `event-demand-api` (Cloud Run service) | FastAPI + React demo (same origin), reads gold live | scale-to-zero (min-instances 0 since 2026-07-08 — cost cut ~$53/mo → ~$0; first hit after idle pays a few s cold start) | gcloud only (not yet in terraform) |

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

As of 2026-07-08 (recovery complete):

| Table | Latest snapshot | Note |
|---|---|---|
| `tm_observations` | 2026-07-08 | CF redeployed 07-04; backfill Jun 19→Jul 1 merged (462,125 rows). The 18:00 PT sweep lands in the NEXT UTC day — fixed by the D8 cadence (15:00 PT) |
| `fact_trends` | 2026-07-06 | daily; Trends' freshest reliable day is always yesterday |
| `fact_trends_daily` | 2026-07-07 | unfrozen 07-08 (manual load); D8 image (`git-77d8e50`) now refreshes it every gold run |
| `fact_youtube` | 2026-07-07 | daily |
| `fact_event_demand` | 2026-07-07 | gold; daily |
| `fact_nineteenhz` / `fact_ra` / `fact_ticketpages` | 2026-07-08 | scene silver, first load 07-08; nightly refresh lands with PR #55 |

Post-fix verification (2026-07-05..07): `gtrends-daily` now lands 392–460
calls/day (was 28–156 pre-6h-window) with 67–74 tier-1 per-DMA units/day,
finishing the whole queue in ~3–4 h (`google_trends_api/check_call_rate.py --days 5`).

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

- `main` — deployed state of record (2026-07-04 collection redesign merged:
  PRs #44–#50 — TM 2×/day cut, gtrends 6h window + tier-1 rotation, headliner
  recovery, 19hz collector + ticket-page JSON-LD poller, RA collector, deploy script).
- `tk/collection-cadence` — D8 cadence — **merged (PR #51), deployed 2026-07-08**.
- `tk/data-review` — July data review — **merged (PR #52)**: raw samples, field
  inventories, price distributions, event trace, 19hz/RA first pulls + overlap.
- `tk/dims-accumulate` — gold-refresh unblock (relationship-test severity) —
  **merged (PR #53), deployed 2026-07-08 (image `git-82cafa2`)**.
- `tk/gold-trends-daily` — gold rewire: `local_interest` from
  `fact_trends_daily` (ibr fallback) + 14-day incremental reprocess window;
  **after merge+deploy run a one-time `dbt build --full-refresh -s fact_event_demand`**
  to backfill history (the trailing window only reaches 14 days back).
- `tk/scene-schedule` — PR #54: 19hz+RA daily Cloud Run jobs + schedulers.
- `tk/scene-silver` — PR #55: scene silver loader + gold-refresh `scene_silver`
  step + `trends_silver` 14-day window (the step was re-downloading ALL ibr
  bronze nightly — 47 min and growing vs the 3600s task timeout).
- `tk/dims-merge-alerting` — PR #56: accumulate fact-referenced dims (MERGE, no
  delete) + project-wide alert on failed Cloud Run job executions.
- Recovery + collection redesign decisions: `collection_efficiency_review.md`.
- Older `tk/*` and `niki/*`, `noam/*` branches are merged feature branches (see PRs #17–#43).

### Pending deploys / user actions (2026-07-08)

D8 cadence rollout — **all four deployed 2026-07-08**:

- [x] TM scheduler → `0 5,15 * * *` PT (gcloud, verified)
- [x] gold-refresh scheduler → `30 16 * * *` PT (gcloud, verified)
- [x] gold-refresh image rebuilt (`:git-77d8e50`) + job updated — the
  `trends_series_silver` step confirmed in the execution log
- [x] `terraform -chdir=terraform/gtrends apply` (gtrends 11:00 PT, youtube 15:00 PT)

New:

- [x] **Fix + redeploy gold-refresh for the dbt relationship-test failures**
  (PR #53 merged; image `git-82cafa2` built + job updated 2026-07-08 —
  verification execution result in the incident log).
- [x] Demo service `event-demand-api` → min-instances 0 (2026-07-08, cost cut)

After the open PRs merge (each deploy needs a go-ahead):

- [ ] PR #54: rebuild the shared ingestion image
  (`gcloud builds submit --config google_trends_api/cloudbuild.yaml .`) +
  `terraform -chdir=terraform/gtrends apply` (2 jobs, 2 schedulers, 1 SA) +
  smoke-execute `nineteenhz-daily` (the RA job's guard no-ops if the day's
  request already landed — correct).
- [ ] PR #55 + #56 + gold rewire: ONE gold-refresh image rebuild + job update
  covers all three; then a one-time
  `dbt build --full-refresh -s fact_event_demand fact_event_demand_continuous`
  to backfill `local_interest` history.
- [ ] PR #56: `terraform -chdir=terraform/gtrends apply` (+1 alert policy —
  can share the apply with #54).

Carried over / done from 2026-07-04:

- [x] Redeploy `ticketmaster-daily-extract` (done 07-04 23:07 UTC via
  `cloud_functions/ticketmaster_daily/deploy.sh`; observations flowing)
- [x] `terraform -chdir=terraform/gtrends apply` — 6h window + rotation (done 07-04; verified at full capacity 07-05..07)
- [x] `tm_observations` bronze backfill Jun 19→Jul 1 (462,125 rows merged 07-04)
- [x] `docs/ra_access_request.md` — **granted** (written OK 2026-07-04, strictly 1 automated request/day; enforced in `ra_api/collect_ra.py`)
- [x] `docs/tm_access_request.md` sent (2026-07-07) — no response yet
- [ ] Wire `ra_api/` + `nineteenhz_api/` collectors into scheduled daily runs + silver joins
- [ ] Optional: official Trends API alpha application
  (developers.google.com/search/apis/trends).

## Incident log

- **2026-07-05 → ongoing: gold-refresh aborts at dbt relationship tests;
  `forecast_event_price` stale since 06-30.** Every scheduled run since 07-05
  fails `relationships(fact_event_demand.artist_id → dim_artist)` and
  `(venue_id → dim_venue)` (24 orphan rows) and exits before `forecast_export`
  — fact tables still advance (dbt materializes before testing), so freshness
  checks alone missed it. Root cause: `fact_event_demand` is incremental
  (point-in-time history) while dims are `WRITE_TRUNCATE` rebuilds from
  *current* `tm_events` — when an event changes venue/headliner, its old fact
  rows reference dim members that vanish from the rebuild (e.g.
  `rZ7HnEZ1AfZbrf` moved venues; its June rows orphaned). The `event_id`
  relationship already had `severity: warn` for exactly this drift; artist/venue
  didn't. Fix: same severity treatment (branch `tk/dims-accumulate`) + backlog:
  make fact-referenced dims accumulate (MERGE, never delete) so drift stops
  growing. Lesson: **freshness ≠ health — check execution status, not just
  `MAX(snapshot_date)`** (the monitoring email fires on function ERRORs, not on
  failed job executions — alert-policy gap to close).
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
