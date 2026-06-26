-- No-row-drop invariant: the gold star keeps EVERY fact_ticketmaster (spine) row.
-- Fails (returns a row) if gold dropped or duplicated any event×snapshot. This is the
-- core gold guarantee: missing Trends/YouTube signals become NULL, never a lost event.
with spine as (select count(*) as n from {{ ref('fact_ticketmaster') }}),
     gold  as (select count(*) as n from {{ ref('fact_event_demand') }})
select
    spine.n as spine_rows,
    gold.n  as gold_rows
from spine
cross join gold
where spine.n != gold.n
