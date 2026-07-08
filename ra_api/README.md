# Resident Advisor collector — one request per day, by written agreement

RA's team granted **written permission (2026-07-04, via Tomas's email) for a
single automated request per day** pulling upcoming SF Bay Area listings for
this (non-commercial, class) project. That's the whole contract:
**never raise the frequency without asking them again**, and keep the data
internal (no redistribution; demo links point back to ra.co). The status log
lives in `docs/ra_access_request.md`.

`collect_ra.py` enforces the limit in code: it checks the bronze
`ra/dt=<today>/` partition and exits if today's request already landed
(`--force` only for retrying a genuinely failed request).

## What one request buys

One GraphQL POST (`eventListings`, area **218 = San Francisco/Oakland**, next
60 days, up to 100 events) returning per event: title, date, times, venue,
full artist lineup, genres, **`attending` count** (a demand signal no other
source has), `isTicketed`, and RA-ticket `cost` text.

## Run

```bash
python ra_api/collect_ra.py --output data/ra/events.csv --land-raw   # THE daily pull
python ra_api/collect_ra.py --lookup-area "san jose"                 # setup-only mode
```

Standard library only (`--land-raw` needs google-cloud-storage). Bronze layout:
`gs://<raw>/ra/dt=<date>/ra_bayarea_<stamp>.json` (untouched GraphQL response,
request variables included for provenance).

Area lookup result (2026-07-04): `{"id": "218", "name": "San Francisco/Oakland"}`.

## Join model

Same as 19hz: `(venue, date)` to `tm_events`/`dim_venue`; lineups feed the
headliner repair. `attending` re-polled daily becomes a time-series per event.

## Backlog

- If 100 events/request proves too small for the 60-day window (totalResults
  reported in the run summary), shrink `--days` — do NOT add pagination
  requests.
- Cloud Run job + terraform after a few clean manual runs (same POC→deploy
  path as the other sources); schedule must stay 1×/day.
