-- Expanded hero-show shortlist: best full-coverage event per artist (deduped),
-- ranked by recognizability proxy (YouTube reach) so the team has range to pick from.
-- Companion to hero_shows.sql (which ranks individual events, Bay-Area first).
--
--   bq --project_id=data-architecture-498123 query --use_legacy_sql=false \
--      < final_presentation/queries/hero_shows_by_artist.sql
--
-- Requires full coverage (price + >=3 trends points + youtube) and a real price (>$1).

with pe as (
  select event_id,
    count(distinct snapshot_date)          as n_snap,
    countif(price_min is not null)         as n_priced,
    countif(local_interest is not null)    as n_interest,
    countif(yt_subscribers is not null)    as n_youtube,
    max(price_max)                         as price_max,
    min(price_min)                         as price_min,
    max(local_interest)                    as interest_max,
    max(yt_subscribers)                    as yt_subs,
    any_value(dma_code)                    as dma_code,
    any_value(artist_id)                   as artist_id
  from `data-architecture-498123.event_demand_analytics.fact_event_demand`
  group by event_id
),
joined as (
  select
    a.artist_name, e.event_name, v.venue_name, v.city, v.state_code, geo.metro_name,
    e.show_date, e.primary_genre, pe.*, (pe.dma_code = '807') as is_bay_area,
    row_number() over (
      partition by a.artist_name
      order by pe.n_interest desc, pe.n_youtube desc, pe.price_max desc
    ) as rn
  from pe
  left join `data-architecture-498123.event_demand_analytics.dim_event`  e   using (event_id)
  left join `data-architecture-498123.event_demand_analytics.dim_artist` a   on a.artist_id = pe.artist_id
  left join `data-architecture-498123.event_demand_analytics.dim_venue`  v   on v.venue_id  = e.venue_id
  left join `data-architecture-498123.event_demand_analytics.dim_geo`    geo on geo.dma_code = pe.dma_code
  where pe.n_priced > 0 and pe.n_interest >= 3 and pe.n_youtube > 0 and pe.price_max > 1
)
select
  artist_name, event_name, venue_name, city, state_code, metro_name, show_date,
  primary_genre, is_bay_area, n_snap, n_interest, n_youtube,
  price_min, price_max, interest_max, yt_subs
from joined
where rn = 1
order by yt_subs desc
limit 30
