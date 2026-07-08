# Data review — July 2026 (what our data looks like, end to end)

Teammate-facing walkthrough of every data source as of **2026-07-08**: the raw
formats, how one event flows bronze → forecast, where coverage stands after the
July fixes, and what the two brand-new sources (19hz, Resident Advisor) add.

All numbers come from two generated companions — refresh them any time:

```bash
python eda/data_review.py                          # -> eda/output/data_review.md
python eda/data_review.py --trace rZ7HnEZ1Af00jd   # -> eda/output/trace_rZ7HnEZ1Af00jd.md
```

Full per-stage transformation SQL: [`../docs/transformations_showcase.md`](../docs/transformations_showcase.md).
Collection decisions D1–D8: [`../docs/collection_efficiency_review.md`](../docs/collection_efficiency_review.md).

---

## 1. What the raw data looks like

Everything lands untouched in `gs://<project>-raw/<source>/dt=<UTC-day>/` before
any parsing — six sources now (three new as of today). Truncated real samples for
each are in [`output/data_review.md`](output/data_review.md) § "Raw-format
samples"; the shapes and gotchas in one screen:

| Source | Raw unit | What to notice |
|---|---|---|
| `ticketmaster` | one JSON array per state per sweep (51 files/run) | `priceRanges` exists only for TM-host **primary** inventory (type is always `standard`; resale occurs 0.0% of the time in our whole corpus); `_embedded.attractions` is the artist join key and is missing on a big chunk of events |
| `google_trends` — `iot_US_` | one artist's **national daily series** (~270 days) per call | values are 0–100 **normalized per pull** — comparable across time within one file, never across artists/geos |
| `google_trends` — `iot_US-XX-DMA` | one (artist, DMA) **daily series** per call | same normalization caveat; one call refreshes the full ~270-day history, which is why the tier-1 rotation only needs to touch each pair every ~4 days |
| `google_trends` — `ibr_DMA_` | one artist's **cross-DMA snapshot** per call | normalized *across DMAs at one moment* — a different scale from iot; the two must never be mixed in one column |
| `youtube` | one rollup file/day, ~400 artist records | channel-level only (subscribers, total views) — no geo, no per-video history; changes slowly |
| `nineteenhz` *(new)* | the untouched Bay Area listing **HTML** page | a curated club/warehouse calendar TM barely sees: price column, full b2b lineups, genre tags, ticket links across 8+ ticketing platforms |
| `ra` *(new)* | one GraphQL JSON response (100 events) | **`attending`** per event — a live social demand counter; strictly **one request/day** (written agreement 2026-07-04), so one 100-event page ≈ 2.5 weeks of Bay Area coverage per day |
| `ticketpages` *(new)* | schema.org JSON-LD scraped from allowlisted ticket pages (eventbrite, shotgun) | per-tier offers **with availability** (`InStock` / `SoldOut`) — the only place we see sell-out state |

## 2. One event, bronze → forecast

[`output/trace_rZ7HnEZ1Af00jd.md`](output/trace_rZ7HnEZ1Af00jd.md) follows
**Everclear with American Hi-Fi @ The Independent, 2026-10-24** (our demo's hero
show) through every layer with real values:

1. **Bronze** — the raw Discovery event: `priceRanges 136.05–236.05`,
   `attractions [Everclear, American Hi-Fi]`, `dates.start.localDate 2026-10-24`.
2. **`tm_events`** (silver, current-state MERGE) — one row, price carried
   forward, `first_seen 2026-06-11`.
3. **`tm_observations`** (silver, honest history) — one row per day the sweep
   actually saw it: 24 days, price flat at 136.05, `n_captures` showing the
   6×/day → 2×/day schedule change, no forward-fill.
4. **Headliner resolution** — `bridge_event_artist` picks Everclear
   (`is_headliner=true`) via the name-hash `artist_id`, which is what joins TM to
   Trends/YouTube.
5. **Demand signals** — `fact_trends_daily` (Everclear × DMA 807: interest
   ~10–13, refreshed on the ~4-day tier-1 rotation) and `fact_youtube`
   (113k subscribers, flat).
6. **`fact_event_demand`** (gold) — one row per (event, snapshot day):
   `days_to_show`, price, `local_interest`, `yt_subscribers`.
7. **`forecast_event_price`** — the precomputed anchor+drift curve
   (~$112 at show day → ~$129 at 90 days out).

**Finding from the trace:** `fact_event_demand.local_interest` is empty on most
recent days — gold still joins the *thin* `fact_trends` cross-DMA snapshot
(one point per artist only on days its `ibr` unit was pulled), while the *dense*
`fact_trends_daily` series (291 days for Everclear) sits unused by gold. The
gold-rewire to `fact_trends_daily` (team-plan follow-on to COLLECT-1) is the
single highest-leverage signal-coverage fix we have queued.

## 3. Coverage — where we stand (vs the 2026-07-04 review)

From [`output/data_review.md`](output/data_review.md) § "Silver/gold coverage":

- **Freshness — fully recovered.** `tm_observations` through **today**
  (2026-07-08, 889k rows; the June deploy-gap freeze is backfilled),
  `fact_trends_daily` through 07-07 (1.62M rows — unfrozen this week and now
  refreshed by every gold run), YouTube/gold through 07-07. Permanent hole:
  Jul 1–3 Trends + YouTube snapshots (billing outage; point-in-time data,
  unrecoverable).
- **Trends collection is at full capacity**: 392–460 calls/day
  (was 28–156 before the 6-hour window fix), whole tier-1 queue finishing in
  ~3–4 h. New cadence (D8, merging now): Trends 11:00 AM PT, TM sweeps
  5:00 AM + 3:00 PM PT, gold 4:30 PM PT — everything same-UTC-day joinable and
  served before 7:00 PM PT.
- **TM pricing is unchanged and structural**: 22.5% of observations priced;
  10,755 of 10,760 ever-priced events were priced from their first observation.
  No amount of re-polling moves this — it's a property of what Ticketmaster
  exposes. (Inventory Status API access requested 07-07; no response yet.)
- **Headliner resolution is still the binding constraint**: 44.2% of priced
  upcoming events resolve a headliner — this caps every Trends/YouTube join,
  including the gold-rewire above. 19hz/RA lineups (below) are the way out for
  Bay Area club shows.

## 4. What 19hz and RA add (first real pulls, 2026-07-08)

Landed today: 19hz **456 events**, RA **100 events** (area 218, one page),
ticket pages **46 offer rows**. The Bay Area cross-source match (normalized
venue + date, shared window 07-08 → 07-24):

| | events in window |
|---|---|
| 19hz | **214** |
| Ticketmaster (DMA 807) | 137 |
| RA | 94 |
| **union** | **383** |

- **TM sees ~36% of the Bay Area scene.** Only **9** of 19hz's 214 events match
  a TM event; 154 are in *no other source*. 19hz+RA overlap on 53 — they
  confirm each other on the club scene TM skips entirely.
- **The new sources are better priced where TM is blind**: 19hz 74.6% priced
  (+17.3% explicitly free), RA 68% with cost, ticket pages 100% (46/46 offers,
  incl. 10 `SoldOut` / 35 `InStock`) — vs TM's 27% in the same market.
- **Unique fields** (nothing in TM has these): 19hz genre tags (100%) + full
  b2b lineups (990 artist credits / 456 events) + age restriction (68%) +
  free-event flag; RA curated subgenres (87%) and **`attending`** (2,700 total
  across 100 events — e.g. *THROTTLE: Marie Vaunt* at 893 attending with a "Low
  Ticket Warning" in the title, two days before the show).

### App-feature ideas this unlocks (data → feature → what it takes)

1. **"Buzz" score on the event page** — RA `attending` (+ its day-over-day
   delta once we have a daily series) is a direct, per-event social demand
   signal; TM gives us nothing comparable. Needs: daily RA pulls into a silver
   `ra_observations` (grain: event × day), join on (venue, date).
2. **Genre filter / genre pages in the demo** — 19hz+RA genre tags cover 100%
   of the club scene vs TM's coarse `genre=Dance/Electronic`. Needs: 19hz+RA
   silver + a small genre dimension.
3. **Sell-out risk flag** — ticket-page `availability` transitions
   (`InStock → SoldOut`) are ground-truth demand outcomes; even as a label for
   validating the forecast they're valuable. Needs: daily poller runs + an
   offers history table.
4. **Free-tonight view** — 19hz `is_free` (17% of events) is a real
   differentiator for the "where do I go tonight" user; zero extra collection.
5. **Headliner-gap fix for club shows** — 19hz/RA lineups joined on
   (venue, date) can hand `tm_events` the artists TM never listed, directly
   raising the 44.2% headliner resolution and thus Trends/YouTube coverage.

## 5. Recommended next steps (in value order)

1. **Wire the gold star to `fact_trends_daily`** (rewire follow-on) — turns the
   trace's empty `local_interest` into a dense daily signal for every tier-1
   artist. No new collection needed.
2. **Schedule `collect_19hz.py` + `collect_ra.py` daily** (Cloud Run jobs like
   the Trends/YouTube pattern, ~08:00 PT so they're comfortably inside the
   one-UTC-day window) and land them into silver tables keyed on
   (venue, date) + name-hash artist ids. RA stays at exactly one request/day.
3. **Backfill nothing, accumulate forward** — both sources are
   point-in-time listings; like Trends snapshots, history starts the day we
   start collecting. Every day we wait is a day of `attending`/availability
   history lost.
4. **Ticket-page poller cadence** — daily after the 19hz pull, allowlist
   unchanged (eventbrite, shotgun), so availability transitions build up.
5. **Then** revisit the headliner-gap join and the genre dimension once a week
   of 19hz/RA history exists to test match rates against.

*Caveat on the overlap numbers: venue matching is exact-after-normalization;
venue aliases under-count matches slightly, so treat only-in-X counts as upper
bounds. The matcher lives in `eda/data_review.py` (`norm_venue`) with unit
tests in `tests/test_data_review.py`.*
