-- Hero-show curation for the final-presentation demo.
--
-- Picks events with the richest coverage across all three source signals so the
-- demo app/charts look complete (not half-empty). A "full coverage" event has, on
-- at least one snapshot, all of: a Ticketmaster price, a Google-Trends local
-- interest value, and a YouTube subscriber count -- and ideally several snapshots
-- so the price/forecast line has a trajectory.
--
-- Deterministic + re-runnable. Run:
--   bq --project_id=data-architecture-498123 query --use_legacy_sql=false \
--      --max_rows=50 < final_presentation/queries/hero_shows.sql
--
-- Bay-Area DMA = 807 (San Francisco-Oakland-San Jose). Adjust the ORDER BY to
-- prefer nationwide instead. Data changes as the pipeline re-runs; re-run to refresh.

with per_event as (
  select
    g.event_id,
    count(distinct g.snapshot_date)                                        as n_snapshots,
    countif(g.price_min is not null)                                       as n_priced,
    countif(g.local_interest is not null)                                  as n_interest,
    countif(g.yt_subscribers is not null)                                  as n_youtube,
    min(g.snapshot_date)                                                   as first_snapshot,
    max(g.snapshot_date)                                                   as last_snapshot,
    max(g.price_min)                                                       as price_min_seen,
    max(g.price_max)                                                       as price_max_seen,
    max(g.local_interest)                                                  as interest_max_seen,
    max(g.yt_subscribers)                                                  as yt_subscribers_seen,
    any_value(g.dma_code)                                                  as dma_code,
    any_value(g.artist_id)                                                 as artist_id
  from `data-architecture-498123.event_demand_analytics.fact_event_demand` g
  group by g.event_id
)

select
  pe.event_id,
  e.event_name,
  a.artist_name,
  v.venue_name,
  v.city,
  v.state_code,
  v.capacity,
  geo.metro_name,
  pe.dma_code,
  e.show_date,
  e.primary_genre,
  pe.n_snapshots,
  pe.n_priced,
  pe.n_interest,
  pe.n_youtube,
  pe.price_min_seen,
  pe.price_max_seen,
  pe.interest_max_seen,
  pe.yt_subscribers_seen,
  pe.first_snapshot,
  pe.last_snapshot,
  (pe.dma_code = '807')                                                    as is_bay_area,
  (pe.n_priced > 0 and pe.n_interest > 0 and pe.n_youtube > 0)             as full_coverage
from per_event pe
left join `data-architecture-498123.event_demand_analytics.dim_event`  e   using (event_id)
left join `data-architecture-498123.event_demand_analytics.dim_artist` a   on a.artist_id = pe.artist_id
left join `data-architecture-498123.event_demand_analytics.dim_venue`  v   on v.venue_id  = e.venue_id
left join `data-architecture-498123.event_demand_analytics.dim_geo`    geo on geo.dma_code = pe.dma_code
where pe.n_priced > 0 and pe.n_interest > 0 and pe.n_youtube > 0   -- full coverage only
order by
  is_bay_area desc,        -- Bay-Area shows first (swap to last for a nationwide pick)
  pe.n_snapshots desc,     -- longer trajectory = better demo line
  pe.n_interest desc,      -- richer geo signal
  pe.price_max_seen desc   -- higher headline price is more eye-catching
limit 40
