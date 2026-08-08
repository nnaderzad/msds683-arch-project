# Curated reference data

## `venue_capacities.csv`

Real venue capacities, hand-curated from public sources (venue sites, Wikipedia,
booking directories, local press) on 2026-08-08 — 312 venues (all Bay Area scene
venues from `fact_nineteenhz` + the top ~120 Ticketmaster venues by event count),
197 with a sourced capacity. Every number carries its `source_url`; conflicting
or multi-room figures are explained in `notes`; venues we could not source keep
an EMPTY capacity (never a guess).

Consumed by:

- `pipeline/silver/build_dimensions.py` — fills `dim_venue.capacity` on a
  (normalized name, state) match during the nightly dims build.
- the synthetic layer (`synth/`) — demand heuristics need room sizes; venues
  without a researched capacity get **tier estimates only inside
  `event_demand_synth`**, labeled `capacity_source='tier_estimate'`.

To extend: add rows (keep `venue_name` exactly as it appears in the source
system so the join holds), re-run `python pipeline/silver/build_dimensions.py`.
