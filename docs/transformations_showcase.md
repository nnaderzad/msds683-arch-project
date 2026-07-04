# Transformations Showcase — sample schemas & SQL

A one-page walk through every transformation in the pipeline, in lineage order.
For each stage: **what it does → sample input schema → the transform → sample
output schema → a sample query**. All column names and SQL are from the real
repo (`pipeline/silver/*.py`, `dbt/models/`, `docs/data-model.md`).

**Medallion shape:** Bronze (raw API JSON in GCS) → Silver (cleaned, conformed,
native grain) → Gold (wide, model-ready). Python builds the silver dims + the
`tm_observations`/`fact_trends`/`fact_youtube` facts; dbt builds
`fact_ticketmaster` and the gold star.

> **Read-me-once caveat for any Trends slide:** `interest` (0–100) is *per-pull
> normalized* — comparable across time for one artist in one metro, **never**
> across artists or DMAs.

```
bronze (raw JSON)
  ├─ TM captures ──► silver.tm_observations ──► dbt silver.fact_ticketmaster ─┐
  ├─ Trends ibr ──► silver.fact_trends                                        │
  ├─ Trends iot ──► silver.fact_trends_daily                                  ├─► gold.fact_event_demand ─► gold.fact_event_demand_continuous
  ├─ YouTube ────► silver.fact_youtube                                        │        (honest star)            (demo-only interior fill)
  └─ tm_events ──► silver dims + bridge ──────────────────────────────────────┘
                                                                              └─► gold.forecast_event_price (Python anchor+drift)
```

---

## STAGE 1 — Bronze → Silver: `tm_observations` (honest TM price history)
**Built by** `pipeline/silver/tm_observations_to_silver.py` ·
**Grain** one row per `(event_id, snapshot_date)` *only for days the sweep
actually observed the event* — **no forward-fill**.

**Sample input** — bronze TM captures (`gs://…-raw/ticketmaster/dt=*/…json`, ~6/day),
shape mirrors the `tm_events` seed:

| event_id | snapshot_date | price_type | price_currency | price_min | price_max | status_code | attraction_names |
|---|---|---|---|---|---|---|---|
| rZ7HnEZ1Af-P_K | 2026-06-11 | standard | USD | 41.38 | 520.85 | onsale | Turnover\|Narrow Head |

**The transform (plain English):** flatten each event (prefer the
`type=="standard"` price range); `aggregate_day` collapses a day's ≤6 captures
into ONE row — **presence = union** of captures, **price = latest priced capture
wins** (NULL if none priced) — and records provenance `n_captures` (1–6) and
`price_disagreed`. PK = `md5("tm"|event_id|snapshot_date)`.

**Sample output** — `tm_observations`:

| tm_obs_id | event_id | snapshot_date | local_date | status_code | price_type | price_currency | price_min | price_max | public_sale_start_utc | public_sale_end_utc | n_captures | price_disagreed | load_ts_utc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Sample query** — how many days did we actually observe each event (the honesty check)?
```sql
select event_id,
       count(*)                       as observed_days,
       min(snapshot_date)             as first_seen,
       max(snapshot_date)             as last_seen,
       countif(price_disagreed)       as days_captures_conflicted
from event_demand_analytics.tm_observations
group by event_id
order by observed_days desc;
```

---

## STAGE 2 — Bronze → Silver: `fact_trends` (local search interest)
**Built by** `pipeline/silver/trends_to_silver.py` ·
**Grain** `(artist_id, dma_code, snapshot_date)`.

**Sample input** — bronze Google Trends `interest_by_region`/DMA snapshot
(committed sample `google_trends_api/sample_data/bay_area_edm_sample.csv`):
```
extract_ts_utc,artist,query,category,geo_name,geo_code,dma,date,granularity,interest,is_partial
2026-06-02T07:25:17+00:00,Skrillex,Skrillex,edm,San Francisco-Oakland-San Jose,US-CA-807,807,2026-03-15,weekly,14,False
```

**The transform:** keep only `interest_by_region` + resolution `DMA` (national
`iot_US` stays in bronze); read `geoCode → dma_code` and the interest value under
the `query` column; `granularity="snapshot"`, `is_partial=False`. PK =
`md5("trends"|artist_id|dma|snapshot_date)`.

**Sample output** — `fact_trends`:

| trends_snapshot_id | artist_id | dma_code | snapshot_date | interest | granularity | is_partial | category | load_ts_utc |
|---|---|---|---|---|---|---|---|---|
| … | art_skrillex | 807 | 2026-06-02 | 14 | snapshot | false | edm | … |

**Sample query** — top metros by interest for one artist on a day:
```sql
select dma_code, interest
from event_demand_analytics.fact_trends
where artist_id = 'art_skrillex' and snapshot_date = date '2026-06-02'
order by interest desc;
```

> Sibling table **`fact_trends_daily`** (built by `trends_series_to_silver.py`)
> has the *same schema* but holds the `iot` daily series (~270 days back), kept
> separate because `iot` normalization differs from `ibr`.

---

## STAGE 3 — Bronze → Silver: `fact_youtube` (channel reach)
**Built by** `pipeline/silver/youtube_to_silver.py` ·
**Grain** `(artist_id, snapshot_date)`.

**Sample input** — bronze YouTube daily roster JSON (`records[]`, keyed by `query`).

**The transform:** read each `records[]` entry, map `query → artist_id`, keep
only the five measures (channel ids/titles dropped — they live on `dim_artist`);
coerce measures to int-or-NULL (hidden subscriber counts stay NULL).

**Sample output** — `fact_youtube`:

| youtube_snapshot_id | artist_id | snapshot_date | official_subscribers | official_total_views | official_video_count | topic_total_views | topic_video_count | load_ts_utc |
|---|---|---|---|---|---|---|---|---|

**Sample query** — subscriber growth for one artist over time:
```sql
select snapshot_date, official_subscribers, official_total_views
from event_demand_analytics.fact_youtube
where artist_id = 'art_skrillex'
order by snapshot_date;
```

---

## STAGE 4 — `tm_events` → Silver conformed dimensions
**Built by** `pipeline/silver/build_dimensions.py` (full-refresh) ·
Produces the six shared dims/bridge the star joins on.

**Sample input** — current-state `tm_events` (note pipe-joined M:M attractions):
```
event_id,event_name,...,venue_id,venue_name,venue_city,venue_state_code,venue_postal_code,
attraction_ids,attraction_names,segment,genre,subgenre,snapshot_date
rZ7HnEZ1Af-P_K,"Turnover, Narrow Head, She's Green",...,rZ7HnEZ17oO1A,The Ritz,San Jose,CA,95113,
K8vZ917ou80|K8vZ9179jVV,Turnover|Narrow Head,Music,Alternative,Alternative Rock,2026-06-11
```
plus reference CSVs: `google_trends_api/reference/dma_geo.csv` (→ `dim_geo`),
`zip_to_dma.csv` (venue geo-resolution).

**The transform (per table):**
- `dim_geo` ← `dma_geo.csv` (rename `dma→dma_code`, `dma_name→metro_name`)
- `dim_date` ← generated calendar `2026-06-01..max(show_date)`, US holidays via `holidays`
- `dim_venue` ← distinct venues; `dma_code` resolved ZIP-first then state fallback
- `dim_artist` ← distinct attractions enriched from roster CSV + YouTube channel cache
- `dim_event` ← one row per `event_id`
- `bridge_event_artist` ← explode `attraction_ids`; `is_headliner` by title name-match (else first), `billing_order` = position

**Sample output** — `bridge_event_artist` (resolves the M:M):

| event_id | artist_id | is_headliner | billing_order |
|---|---|---|---|
| rZ7HnEZ1Af-P_K | art_turnover | true | 0 |
| rZ7HnEZ1Af-P_K | art_narrow_head | false | 1 |

**Sample query** — the headliner for each event:
```sql
select event_id, artist_id
from event_demand_analytics.bridge_event_artist
where is_headliner
qualify row_number() over (partition by event_id order by billing_order) = 1;
```

---

## STAGE 5 — Silver (dbt): `fact_ticketmaster` (the price spine)
**Model** `dbt/models/silver/fact_ticketmaster.sql` ·
`source('silver','tm_observations')` → price-history fact at grain
`event_id × snapshot_date`. Incremental **merge** on `tm_snapshot_id`, partitioned
by `snapshot_date`, clustered by `event_id`. **Still no forward-fill** — this is
the spine the gold star left-joins onto.

**The transform (real SQL):**
```sql
-- deduped: guard the grain so the surrogate key is unique no matter the source
qualify row_number() over (
    partition by event_id, snapshot_date order by show_date desc) = 1
-- final projection:
select
  to_hex(md5(concat(event_id,'|',cast(snapshot_date as string)))) as tm_snapshot_id,
  event_id, snapshot_date, status_code, price_type, price_currency,
  price_min, price_max, public_sale_start_utc, public_sale_end_utc,
  date_diff(show_date, snapshot_date, day)  as days_to_show,   -- derived
  n_captures, price_disagreed
from deduped
{% if is_incremental() %}
where snapshot_date > (select coalesce(max(snapshot_date), date '1900-01-01') from {{ this }})
{% endif %}
```

**Sample output** — `fact_ticketmaster`:

| tm_snapshot_id (PK) | event_id | snapshot_date | status_code | price_type | price_currency | price_min | price_max | public_sale_start_utc | public_sale_end_utc | days_to_show | n_captures | price_disagreed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Sample query** — price trajectory of one show as the date approaches:
```sql
select snapshot_date, days_to_show, price_min, price_max
from event_demand_analytics.fact_ticketmaster
where event_id = 'rZ7HnEZ1Af-P_K'
order by snapshot_date;
```

---

## STAGE 6 — Gold (dbt): `fact_event_demand` (the honest star)
**Model** `dbt/models/gold/fact_event_demand.sql` · one wide row per event per
snapshot. The `fact_ticketmaster` spine is kept whole; demand signals are
LEFT-JOIN enrichments on the **headliner** artist + venue DMA (NULL where a
signal wasn't collected). **No-row-drop invariant** — tested by
`dbt/tests/assert_gold_rows_eq_spine.sql`.

**The transform (real SQL):**
```sql
-- headliner: exactly one artist per event
select event_id, artist_id from {{ source('silver','bridge_event_artist') }}
where is_headliner
qualify row_number() over (partition by event_id order by billing_order, artist_id) = 1
-- event_venue: dim_event LEFT JOIN dim_venue using(venue_id) -> dma_code
select
  spine.event_id, spine.snapshot_date, h.artist_id, ev.venue_id, ev.dma_code,
  spine.days_to_show, spine.price_min, spine.price_max, spine.price_currency, spine.status_code,
  ft.interest             as local_interest,   -- fact_trends (artist + dma + date)
  fy.official_subscribers as yt_subscribers,    -- fact_youtube (artist + date)
  fy.official_total_views as yt_views
from spine
left join headliner h    on h.event_id = spine.event_id
left join event_venue ev on ev.event_id = spine.event_id
left join {{ source('silver','fact_trends') }} ft
       on ft.artist_id=h.artist_id and ft.dma_code=ev.dma_code and ft.snapshot_date=spine.snapshot_date
left join {{ source('silver','fact_youtube') }} fy
       on fy.artist_id=h.artist_id and fy.snapshot_date=spine.snapshot_date
```

**Sample output** — `fact_event_demand`:

| event_id | snapshot_date | artist_id | venue_id | dma_code | days_to_show | price_min | price_max | price_currency | status_code | local_interest | yt_subscribers | yt_views |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Sample query** — price vs. demand signals for one show, already pre-joined:
```sql
select snapshot_date, days_to_show, price_min, local_interest, yt_subscribers
from event_demand_analytics.fact_event_demand
where event_id = 'rZ7HnEZ1Af-P_K'
order by snapshot_date;
```

---

## STAGE 7 — Gold (dbt): `fact_event_demand_continuous` (demo-only fill)
**Model** `dbt/models/gold/fact_event_demand_continuous.sql` · materialized as a
table. `fact_event_demand` stays strictly honest (model trains on real
observations); for the demo chart this fills **interior-only** price gaps
(carry-forward between an event's first and last observed day) and flags every
filled row with `price_is_filled`. Demand signals stay observed-only.

**The transform (real SQL):**
```sql
-- bounds: per-event observed envelope + recovered show_date
max(case when days_to_show is not null
         then date_add(snapshot_date, interval days_to_show day) end) as show_date
-- calendar: one row per event per day in [first_obs, last_obs]  (interior-only by construction)
from bounds b, unnest(generate_date_array(b.first_obs, b.last_obs)) as day
-- filled: carry last-known price forward across interior gaps
last_value(price_min ignore nulls) over w as price_min   -- (also max, currency, status, ids)
window w as (partition by event_id order by snapshot_date
             rows between unbounded preceding and current row)
-- flag: true exactly when today's price was carried forward, not observed
(obs_price_min is null and price_min is not null) as price_is_filled
```
Contract enforced by `dbt/tests/assert_continuous_fill_labeled.sql`
(no fill without a price, span == observed envelope, unique grain).

**Sample output** — adds `price_is_filled` to the star's columns:

| event_id | snapshot_date | … | price_min | … | price_is_filled |
|---|---|---|---|---|---|

**Sample query** — continuous demo line, showing which points are real vs filled:
```sql
select snapshot_date, price_min, price_is_filled
from event_demand_analytics.fact_event_demand_continuous
where event_id = 'rZ7HnEZ1Af-P_K'
order by snapshot_date;
```

---

## STAGE 8 — Gold (Python): `forecast_event_price`
**Built by** `pipeline/gold/export_predictions_table.py` (`model/features.py` +
`train.py` + `predict.py`). Assembles per-show price forecasts from the gold star
using an **anchor-on-real-price + drift** model.

**Sample output** — `forecast_event_price`:

| event_id | days_to_show | predicted_price |
|---|---|---|
| rZ7HnEZ1Af-P_K | 30 | 45.77 |

**Sample query** — predicted price curve for a show:
```sql
select days_to_show, predicted_price
from event_demand_analytics.forecast_event_price
where event_id = 'rZ7HnEZ1Af-P_K'
order by days_to_show desc;
```

---

## dbt data tests (proof the transforms are correct)
- `assert_gold_rows_eq_spine.sql` — gold has exactly one row per spine row (no drops, no fan-out).
- `assert_continuous_fill_labeled.sql` — every forward-filled row is labeled and bounded to the observed envelope.
- Column tests: `tm_snapshot_id` unique+not_null; `days_to_show` range [-3650, 3650]; `(event_id, snapshot_date)` unique on both gold tables; `price_is_filled` ∈ {true, false}.
