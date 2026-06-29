-- Gold CONTINUITY view (DEMO ONLY, TEAM-DERIVED -- not raw source data).
--
-- `fact_event_demand` is kept strictly HONEST: a row only for days the sweep actually
-- observed an event (no forward-fill), so the forecast model trains on real observations.
-- For the demo price chart we want a *continuous* daily line per show, so this model fills
-- the INTERIOR gaps (days between an event's first and last OBSERVED day) by carrying the
-- last-known price forward, and flags every such row with `price_is_filled = true`.
--
-- Honesty guarantees:
--   * INTERIOR ONLY -- the per-event calendar runs first_observed..last_observed, so nothing
--     is fabricated before the event was first seen or after it was last seen.
--   * `price_is_filled` is true exactly when the displayed price was NOT observed that day
--     (carried forward); false when the price was observed on that snapshot_date.
--   * Demand signals (local_interest, yt_*) are left observed-only (NULL on filled days) --
--     only the price line is made continuous.
{{
  config(
    materialized='table',
    partition_by={'field': 'snapshot_date', 'data_type': 'date'},
    cluster_by=['event_id']
  )
}}

with observed as (

    select * from {{ ref('fact_event_demand') }}

),

bounds as (

    select
        event_id,
        min(snapshot_date) as first_obs,
        max(snapshot_date) as last_obs,
        -- show_date is constant per event; recover it from any observed priced/dated row
        max(case when days_to_show is not null
                 then date_add(snapshot_date, interval days_to_show day) end) as show_date
    from observed
    group by event_id

),

calendar as (

    -- One row per event per day in its OBSERVED envelope (interior-only by construction).
    select b.event_id, day as snapshot_date, b.show_date
    from bounds b,
         unnest(generate_date_array(b.first_obs, b.last_obs)) as day

),

joined as (

    select
        c.event_id,
        c.snapshot_date,
        c.show_date,
        o.event_id is not null  as was_observed,
        o.price_min             as obs_price_min,   -- same-day observed price (NULL if missing/unpriced)
        o.artist_id,
        o.venue_id,
        o.dma_code,
        o.price_max,
        o.price_currency,
        o.status_code,
        o.local_interest,
        o.yt_subscribers,
        o.yt_views
    from calendar c
    left join observed o
        on  o.event_id      = c.event_id
        and o.snapshot_date = c.snapshot_date

),

filled as (

    select
        event_id,
        snapshot_date,
        show_date,
        obs_price_min,
        -- constant event attributes, carried across interior gaps
        last_value(artist_id  ignore nulls) over w as artist_id,
        last_value(venue_id   ignore nulls) over w as venue_id,
        last_value(dma_code   ignore nulls) over w as dma_code,
        -- price + status forward-filled (last-known carried into interior gaps)
        last_value(obs_price_min   ignore nulls) over w as price_min,
        last_value(price_max       ignore nulls) over w as price_max,
        last_value(price_currency  ignore nulls) over w as price_currency,
        last_value(status_code     ignore nulls) over w as status_code,
        -- demand signals stay observed-only (not forward-filled)
        local_interest,
        yt_subscribers,
        yt_views
    from joined
    window w as (
        partition by event_id order by snapshot_date
        rows between unbounded preceding and current row
    )

)

select
    event_id,
    snapshot_date,
    artist_id,
    venue_id,
    dma_code,
    date_diff(show_date, snapshot_date, day) as days_to_show,
    price_min,
    price_max,
    price_currency,
    status_code,
    local_interest,
    yt_subscribers,
    yt_views,
    -- TEAM-DERIVED flag: true when the displayed price was carried forward, not observed today
    (obs_price_min is null and price_min is not null) as price_is_filled
from filled
