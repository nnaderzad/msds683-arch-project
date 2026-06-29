# TODO: enforce the show-date cutoff server-side & save API calls

## Background

A past show (e.g. *Shakey Graves – Fondness, Etc. Tour, The Hall, Little Rock AR,
Jun 17 2026*) was plotting points **after** its show date — both Local/Global popularity
and the Observed lowest-price line.

Root cause: the API (`api/gold.py`) returns *every* snapshot for an event with no cutoff.
Trends/YouTube collect per **artist × metro** — a series shared across all of an artist's
events — so artist-level snapshots taken after the show get joined back to the past event
and served.

**Shipped (UI-only, low-risk):** the chart now drops post-show points in
`web/src/utils/chartData.ts` (`buildDemandChartData`) and adds a "Today" reference line
that renders only for upcoming shows (`web/src/components/DemandSignalsChart.tsx`). This is
purely presentational — the API still serves post-show rows.

## Deferred work

### 1. API cutoff (authoritative)

In `api/gold.py` `GoldRepository.__init__`, filter `fact` once before `_build_latest()`
runs, using a null-safe predicate so rows with a missing `days_to_show` are kept:

```python
from dataclasses import replace
fact = frames.fact
if "days_to_show" in fact.columns:
    fact = fact[~(pd.to_numeric(fact["days_to_show"], errors="coerce") < 0)]
self._frames = replace(frames, fact=fact)
```

Both `_history()` and `_build_latest()` read `self._frames.fact`, so they inherit the
cutoff. Once this lands, the UI filter in `buildDemandChartData` becomes redundant (safe to
keep as defense-in-depth).

Test: extend `tests/test_api.py` `fixture_frames()` with a past event that has both a
pre-show snapshot (`days_to_show >= 0`) and a post-show snapshot (`days_to_show < 0`); assert
`GET /show/{id}` `history` contains only the pre-show row(s) and the summary metrics reflect
the show-date snapshot.

### 2. Collection savings (the original "save API calls" ask)

Trends/YouTube collect per artist × metro, shared across an artist's events, so a single
past show **cannot** be suppressed without affecting that artist's other upcoming events.

- `google_trends_api/build_roster.py` already drops all-past artists via
  `local_date >= CURRENT_DATE()`.
- Future work: tighten per-(artist, metro) targeting in `build_frames` / `build_queue` so a
  metro drops out once its only upcoming show in that metro has passed.
- Optionally cap the gold dbt model `fact_event_demand_continuous.sql` with
  `WHERE days_to_show >= 0` so post-show rows never enter the warehouse at all.

Per-artist granularity is the constraint to design around.
