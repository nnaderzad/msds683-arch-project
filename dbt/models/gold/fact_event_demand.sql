-- Gold star: one wide row per upcoming event per daily snapshot (docs/data-model.md §4).
-- The fact_ticketmaster spine is kept whole; the other source signals are LEFT-JOIN
-- enrichments on the **headliner** artist + the event's venue DMA, NULL where a signal
-- wasn't collected. Every spine row survives (the no-row-drop invariant) — see the
-- singular test tests/assert_gold_rows_eq_spine.sql.
{{
  config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['event_id', 'snapshot_date'],
    partition_by={'field': 'snapshot_date', 'data_type': 'date'},
    cluster_by=['event_id'],
    on_schema_change='append_new_columns'
  )
}}

with spine as (

    select
        event_id,
        snapshot_date,
        days_to_show,
        price_min,
        price_max,
        price_currency,
        status_code
    from {{ ref('fact_ticketmaster') }}
    {% if is_incremental() %}
    -- Reprocess a trailing window, not just new dates: the daily Trends series lands up
    -- to ~4 days late (tier-1 pair rotation; the loader's 14-day capture window in
    -- pipeline/gold_refresh.py TRENDS_SERIES_LOOKBACK_DAYS). The MERGE on
    -- (event_id, snapshot_date) makes the re-run idempotent — rows are updated in
    -- place as late signals arrive, never duplicated.
    where snapshot_date > date_sub(
        (select coalesce(max(snapshot_date), date '1900-01-01') from {{ this }}),
        interval 14 day)
    {% endif %}

),

headliner as (

    -- One artist per event (A3 guarantees a single is_headliner; dedupe defensively so a
    -- data blip can never fan the spine out and break the no-row-drop invariant).
    select event_id, artist_id
    from {{ source('silver', 'bridge_event_artist') }}
    where is_headliner
    qualify row_number() over (partition by event_id order by billing_order, artist_id) = 1

),

event_venue as (

    select e.event_id, e.venue_id, v.dma_code
    from {{ source('silver', 'dim_event') }} e
    left join {{ source('silver', 'dim_venue') }} v using (venue_id)

)

select
    spine.event_id,
    spine.snapshot_date,
    h.artist_id,
    ev.venue_id,
    ev.dma_code,
    spine.days_to_show,
    spine.price_min,
    spine.price_max,
    spine.price_currency,
    spine.status_code,
    -- Daily iot series first (coherent per-(artist,dma) trajectory, dense for tier-1
    -- pairs), ibr snapshot as fallback (broad cross-DMA coverage on days a snapshot
    -- lands). Both are 0-100 per-pull Trends interest; fact_trends was already mixing
    -- pulls across days, so the fallback doesn't add a new scale caveat — see the
    -- Trends normalization note in CLAUDE.md.
    coalesce(td.interest, ft.interest) as local_interest,
    fy.official_subscribers   as yt_subscribers,    -- fact_youtube (artist + date)
    fy.official_total_views   as yt_views
from spine
left join headliner h
    on h.event_id = spine.event_id
left join event_venue ev
    on ev.event_id = spine.event_id
left join {{ source('silver', 'fact_trends_daily') }} td
    on  td.artist_id     = h.artist_id
    and td.dma_code      = ev.dma_code
    and td.snapshot_date = spine.snapshot_date
left join {{ source('silver', 'fact_trends') }} ft
    on  ft.artist_id     = h.artist_id
    and ft.dma_code      = ev.dma_code
    and ft.snapshot_date = spine.snapshot_date
left join {{ source('silver', 'fact_youtube') }} fy
    on  fy.artist_id     = h.artist_id
    and fy.snapshot_date = spine.snapshot_date
