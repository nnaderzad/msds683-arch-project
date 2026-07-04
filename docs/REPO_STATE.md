# Repo state — read this first

> **⚠️ Maintenance rule (agents and humans):** update this file as part of **every
> commit / PR** that changes pipeline behavior, deploys anything, moves data
> coverage, or changes project status. Stale entries are worse than none — fix or
> delete what you can't verify. Refresh the "Last verified" stamps when you
> re-check a section.

**Last full review:** 2026-07-04 (billing-outage recovery + collection-efficiency review)

## What this is

Event-demand forecasting data architecture for Bay Area electronic-music events
(MSDS 683). Medallion lake on GCP + BigQuery star schema + anchor-and-drift price
forecaster + public demo dashboard. Deep dives:

- Architecture narrative: [`../README.md`](../README.md)
- Schema (silver constellation + gold star): [`data-model.md`](data-model.md)
- Stage-by-stage pipeline walkthrough: [`transformations_showcase.md`](transformations_showcase.md)
- Collection-efficiency decision record (2026-07): [`collection_efficiency_review.md`](collection_efficiency_review.md)

## Live system (GCP project `data-architecture-498123`, us-west1)

**Billing:** `BillingAcctForEdu_MSDS692` (`01EB77-4F3D56-814EA1`), linked 2026-07-04
after the previous account closed (see incident log).

| Component | What | Schedule (PT) | Deployed via |
|---|---|---|---|
| `ticketmaster-daily-extract` (Cloud Function gen2) | nationwide Discovery sweep → bronze + `tm_events` + `tm_observations` | 06:00, 18:00 (cut from every-4h 2026-07-04 — daily-grain observations made 6×/day ~5× redundant) | `terraform/` (state local, on Niki's machine) or gcloud |
| `gtrends-daily` (Cloud Run job) | Trends national + DMA-snapshot units → bronze + silver | 09:00 | `terraform/gtrends/` (remote state, anyone can apply) |
| `gtrends-backfill` (Cloud Run job) | deep per-DMA daily series, on demand | manual | `terraform/gtrends/` |
| `youtube-daily` (Cloud Run job) | channel stats + topic views → bronze + `fact_youtube` | 09:30 | `terraform/gtrends/` |
| `gold-refresh` (Cloud Run job) | silver loaders → dbt build → forecast → GX gate | 09:00 | `terraform/` |
| `event-demand-api` (Cloud Run service) | FastAPI + React demo (same origin), reads gold live | always on (min-instances 1) | gcloud only (not yet in terraform) |

Data lands in `gs://data-architecture-498123-{raw,processed,analytics}` and
BigQuery dataset `event_demand_analytics`.

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

As of 2026-07-04 (mid-recovery):

| Table | Latest snapshot | Note |
|---|---|---|
| `tm_observations` | 2026-06-18 | deploy-gap freeze; backfill Jun 19→Jul 1 in progress; CF redeploy pending |
| `fact_trends` | 2026-06-29 | resumes with `gtrends-daily` |
| `fact_trends_daily` | 2026-06-15 | only fed by backfill-mode runs (daily-mode dma units planned) |
| `fact_youtube` | 2026-06-30 | resumes with `youtube-daily` |
| `fact_event_demand` | 2026-06-30 | gold; refreshes after silver recovers |

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

- `main` — deployed state of record.
- `tk/repo-state-docs` — docs refresh + the 2026-07-04 collection redesign
  (TM 2×/day cut, gtrends 6h window + tier-1 rotation, headliner recovery,
  19hz collector + ticket-page JSON-LD poller, probes/EDA).
- Recovery + collection redesign decisions: `collection_efficiency_review.md`.
- Older `tk/*` and `niki/*`, `noam/*` branches are merged feature branches (see PRs #17–#43).

### Pending deploys / user actions (2026-07-04)

- [ ] **Redeploy `ticketmaster-daily-extract`** (observations fix; gcloud command
  in the session notes / `cloud_functions/ticketmaster_daily/README.md`) — until
  then `tm_observations` doesn't advance.
- [ ] **`terraform -chdir=terraform/gtrends apply`** — activates the 6h daily
  window + tier-1 rotation (image already rebuilt in Artifact Registry).
- [ ] `tm_observations` bronze backfill Jun 19→Jul 1 (loader run in progress 07-04).
- [ ] Send `docs/tm_access_request.md` (Inventory Status API + quota) and
  `docs/ra_access_request.md` (RA academic permission).
- [ ] Optional: official Trends API alpha application
  (developers.google.com/search/apis/trends).

## Incident log

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
