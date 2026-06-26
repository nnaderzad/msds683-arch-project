-- Silver fact: Ticketmaster price *history* at the locked grain (event x snapshot_date).
-- Source = the processed daily parquet snapshots (read as a BigQuery external table);
-- snapshot_date is the dt= partition recovered from _FILE_NAME. This is the spine the
-- gold star (B1) left-joins onto -- distinct from current-state tm_events.
--
-- Incremental + idempotent: new daily snapshots append; re-running an already-loaded day
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

with snapshots as (

    select
        regexp_extract(_FILE_NAME, r'dt=([0-9]{4}-[0-9]{2}-[0-9]{2})') as snapshot_date_str,
        event_id,
        local_date,            -- the show date (per snapshot row)
        status_code,
        price_type,
        price_currency,
        price_min,
        price_max,
        public_sale_start_utc,
        public_sale_end_utc
    from {{ source('ticketmaster_raw', 'tm_snapshots_ext') }}

),

typed as (

    select
        event_id,
        safe_cast(snapshot_date_str as date)        as snapshot_date,
        safe_cast(local_date as date)               as show_date,
        nullif(status_code, '')                     as status_code,
        nullif(price_type, '')                      as price_type,
        nullif(price_currency, '')                  as price_currency,
        safe_cast(price_min as float64)             as price_min,
        safe_cast(price_max as float64)             as price_max,
        safe_cast(public_sale_start_utc as timestamp) as public_sale_start_utc,
        safe_cast(public_sale_end_utc as timestamp)   as public_sale_end_utc
    from snapshots
    where event_id is not null
      and snapshot_date_str is not null

),

deduped as (

    -- A daily file is already one row per event, but guard the (event_id, snapshot_date)
    -- grain so tm_snapshot_id stays unique no matter what the source does.
    select *
    from typed
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
    date_diff(show_date, snapshot_date, day) as days_to_show
from deduped

{% if is_incremental() %}
where snapshot_date > (select coalesce(max(snapshot_date), date '1900-01-01') from {{ this }})
{% endif %}
