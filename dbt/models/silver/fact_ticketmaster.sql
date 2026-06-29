-- Silver fact: Ticketmaster price *history* at the locked grain (event x snapshot_date).
-- Source = the HONEST `silver.tm_observations` table (built from raw bronze by
-- pipeline/silver/tm_observations_to_silver.py): one row per event per day the sweep
-- ACTUALLY observed it, price as observed (NULL if observed unpriced). **No forward-fill.**
-- This replaced the old `tm_snapshots_ext` parquet, which was an export of current-state
-- tm_events and carried last-known prices forward into days the sweep never re-observed
-- (eda/diagnose_price_gaps.py proved that artifact: 0 interior / 0 trailing gaps).
-- This is the spine the gold star (B1) left-joins onto -- distinct from current-state tm_events.
--
-- Incremental + idempotent: new observed days append; re-running an already-loaded day
-- is a no-op. `dbt build --full-refresh` rebuilds the whole history.
{{
  config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='tm_snapshot_id',
    partition_by={'field': 'snapshot_date', 'data_type': 'date'},
    cluster_by=['event_id'],
    on_schema_change='append_new_columns'
  )
}}

with observations as (

    select
        event_id,
        snapshot_date,         -- the day the event was observed (typed DATE from the loader)
        local_date as show_date,
        status_code,
        price_type,
        price_currency,
        price_min,
        price_max,
        public_sale_start_utc,
        public_sale_end_utc,
        n_captures,            -- provenance: how many of the day's runs saw it (1-6)
        price_disagreed        -- provenance: the day's captures conflicted on price/presence
    from {{ source('silver', 'tm_observations') }}
    where event_id is not null
      and snapshot_date is not null

),

deduped as (

    -- The loader already collapses to one row per (event_id, snapshot_date); guard the
    -- grain anyway so tm_snapshot_id stays unique no matter what the source does.
    select *
    from observations
    qualify row_number() over (
        partition by event_id, snapshot_date
        order by show_date desc
    ) = 1

)

select
    to_hex(md5(concat(event_id, '|', cast(snapshot_date as string)))) as tm_snapshot_id,
    event_id,
    snapshot_date,
    status_code,
    price_type,
    price_currency,
    price_min,
    price_max,
    public_sale_start_utc,
    public_sale_end_utc,
    date_diff(show_date, snapshot_date, day) as days_to_show,
    n_captures,
    price_disagreed
from deduped

{% if is_incremental() %}
where snapshot_date > (select coalesce(max(snapshot_date), date '1900-01-01') from {{ this }})
{% endif %}
