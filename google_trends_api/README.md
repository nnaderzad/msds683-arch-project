# Google Trends ingestion — local search-interest signal

The **geographic + historical backbone** of the event-demand pipeline. Google
Trends is the project's only source of *per-metro* artist popularity **and** the
only one with real history (Spotify had no geo; the YouTube Data API has neither),
so it carries the "is this artist popular *here*?" signal the demand model needs.

See [`DEPLOY.md`](DEPLOY.md) for deploying the cloud jobs.

## What it does

```
BigQuery tm_events ──► roster (artists w/ upcoming shows + their DMAs)
   (Ticketmaster)        │  ranked, soonest-show-first
                         ▼
                   pytrends fetch ──► BRONZE  gs://…-raw/google_trends/dt=<date>/…json
                   national daily  │            (one (artist, geo, endpoint) per file)
                   + DMA snapshot   │
                   + per-DMA daily  ▼
                            joins to Ticketmaster on (artist, DMA, date)
```

- **Roster from Ticketmaster.** We don't hand-maintain an artist list — we read
  the silver `tm_events` table for artists with upcoming shows and the DMAs they
  play, rank by touring footprint (distinct markets) and soonest show, and select
  the top N plus a curated EDM/pop seed. So Trends covers exactly the acts the rest
  of the pipeline tracks, and the data joins by construction.
- **Two fetch units per artist** (cheap): `interest_over_time(geo="US")` daily for
  a ~270-day window, and `interest_by_region(resolution="DMA")` — one call returns
  all ~210 DMAs (the geographic distribution). Optionally a deep **per-DMA daily**
  series for the metros each artist plays.
- **Bronze landing** via [`../common/gcs_io.py`](../common/gcs_io.py), same
  `dt=`-partitioned layout as the Ticketmaster pipeline.

## Components

| File | Role |
|---|---|
| `config.py` | `Metro`/`Artist` dataclasses; curated seed roster; 10 anchor metros |
| `build_dma_table.py` | generate `reference/dma_geo.csv` (210 DMAs → `US-<ST>-<DMA>` geo codes) from one Trends call |
| `build_zip_dma_table.py` | generate `reference/zip_to_dma.csv` (~41k ZIPs → DMA) from a public crosswalk |
| `geo_lookup.py` | resolve a Ticketmaster venue (ZIP, then state) → DMA + geo code |
| `build_roster.py` | query `tm_events`, explode artists, map venues → DMA, rank, write the roster |
| `trends_client.py` | pytrends wrapper: `interest_over_time` / `interest_by_region`, retry/backoff |
| `fetch_and_land.py` | the fetch+land core — one `run_unit` per (artist, geo, endpoint) |
| `job.py` | Cloud Run Job entrypoint (backfill / daily), sharded + idempotent |
| `Dockerfile`, `cloudbuild.yaml`, `DEPLOY.md` | container + deploy |

`reference/*.csv` are committed, provenance-documented lookup tables so the roster
is reproducible without re-hitting the rate-limited endpoint. Full local pulls go
to a gitignored repo-root `data/`; small samples are in `sample_data/`.

## Run locally

```bash
conda activate music-demand   # env: ../environment.yml

# 1. (Re)generate the committed geo reference tables — run once / to refresh:
python google_trends_api/build_dma_table.py
python google_trends_api/build_zip_dma_table.py

# 2. Build the roster from the Ticketmaster silver table:
python google_trends_api/build_roster.py --top-n 250          # or --states CA for a smoke

# 3. Fetch + land (dry-run prints, omit --dry-run to write to the bucket):
python google_trends_api/fetch_and_land.py --limit 3 --modes national snapshot --dry-run

# Run the Cloud Run job logic locally, bounded:
JOB_MODE=daily MAX_UNITS=4 STATES=CA python google_trends_api/job.py
```

## Deployed pipeline

Provisioned in [`../terraform/gtrends/`](../terraform/gtrends/) (isolated root,
remote GCS state) — see [`DEPLOY.md`](DEPLOY.md):

- **`gtrends-backfill`** Cloud Run Job — deep history pass, run on demand, sharded
  across tasks. `INCLUDE_DMA=false` (default) does national + DMA snapshot;
  `true` adds the per-DMA daily series.
- **`gtrends-daily`** Cloud Run Job — scheduled daily refresh (national + DMA
  snapshot at daily resolution).
- Idempotent: a run skips units already landed in today's `dt=` partition, so
  retries/restarts resume instead of re-spending Trends calls.

## Output schema (bronze JSON)

One file per `(artist, geo, endpoint)`; each holds metadata + `records`:

```json
{ "source": "google_trends", "endpoint": "interest_over_time",
  "artist": "Journey", "query": "Journey", "geo": "US", "resolution": "national",
  "timeframe": "2025-09-18 2026-06-14", "granularity": "daily",
  "records": [ { "date": "2025-09-18T00:00:00.000", "Journey": 63, "isPartial": false }, … ] }
```

`interest_by_region` records carry `geoName` + `geoCode` (the DMA number) → joins
to `reference/dma_geo.csv` / `zip_to_dma.csv` and thus to Ticketmaster venues.

## Things to know (Google Trends quirks)

- **No official API.** We use `pytrends` (the website's private endpoint):
  rate-limited, occasional HTTP 429 → we pace politely and back off. Pinned combo
  `pytrends==4.9.2` + `pandas==3.0.3` (Python 3.11).
- **Values are relative 0–100** within each `(query, geo, timeframe)` pull —
  comparable across *time* for one artist in one metro, **not** across artists or
  metros. Treat each series as its own index; don't sum/average across series.
- **Geo codes are `US-<STATE>-<DMA>`** (e.g. `US-CA-807`); bare `US-<DMA>` → 400.
  Daily granularity is only returned for windows ≤ ~269 days.
- **`interest_by_region` is the efficiency win** — all DMAs in one call — so we
  use it for the geographic distribution and reserve per-DMA `interest_over_time`
  for the deep pass.
- **Ambiguous names** (short/common words) pick up unrelated searches; the curated
  seed carries `query` overrides (e.g. `Fisher` → `"Fisher DJ"`). TM-derived names
  are used as-is, so some noise remains.
- Do **not** pass `retries=`/`backoff_factor=` to `TrendReq(...)` — pytrends 4.9.2
  builds a urllib3 `Retry` with the removed `method_whitelist` kwarg and crashes on
  urllib3 2.x. We do retry/backoff ourselves in `trends_client.py`.
