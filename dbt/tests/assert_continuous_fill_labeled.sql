-- Contract for the team-derived gold continuity table. Fails (returns rows) if:
--   1. fill_no_price  — a row flagged price_is_filled carries no price (nothing to fill).
--   2. envelope       — the continuous span isn't exactly the event's OBSERVED span
--                       (interior-only: never fabricate before first / after last observed).
--   3. grain          — (event_id, snapshot_date) is not unique.
with cont as (
    select * from {{ ref('fact_event_demand_continuous') }}
),

obs_bounds as (
    select event_id, min(snapshot_date) as lo, max(snapshot_date) as hi
    from {{ ref('fact_event_demand') }}
    group by event_id
),

cont_bounds as (
    select event_id, min(snapshot_date) as lo, max(snapshot_date) as hi
    from cont
    group by event_id
),

fill_no_price as (
    select 'fill_no_price' as violation, event_id, cast(snapshot_date as string) as detail
    from cont
    where price_is_filled and price_min is null
),

envelope as (
    select 'envelope' as violation, o.event_id, cast(o.lo as string) as detail
    from obs_bounds o
    join cont_bounds c using (event_id)
    where o.lo != c.lo or o.hi != c.hi
),

grain as (
    select 'grain' as violation, event_id, cast(snapshot_date as string) as detail
    from cont
    group by event_id, snapshot_date
    having count(*) > 1
)

select * from fill_no_price
union all select * from envelope
union all select * from grain
