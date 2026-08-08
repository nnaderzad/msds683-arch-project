# synth/ — synthetic demand layer (`event_demand_synth`)

Generates the synthetic dataset **without polluting the honest warehouse**: a
hybrid world where everything knowable stays REAL (events, artists, venues,
researched capacities, observed prices) and only what no source provides is
synthesized — sellout timing and the resale market (measured **0.0%** resale
`priceRanges` in TM bronze). The generator writes exclusively to the separate
`event_demand_synth` dataset; synthetic values NEVER land in
`event_demand_analytics`.

## Design

- `heuristics.py` — the "physics": pure, seeded, offline-tested functions
  encoding the team's observed market anecdotes. Popular act + small room sells
  out fast and resells ABOVE face (MGMT at Public Works); soft-demand big room
  resells BELOW face (Chris Lake, $100 face → $50–60 day-of); festival days
  anchor to the summed draw of that day's headliners (Outside Lands). Every
  outcome routes through one scalar: `demand_ratio` = expected draw ÷ capacity.
- `generate.py` — reads the real star, applies the heuristics per event, loads
  two WRITE_TRUNCATE tables:
  - `synth_event_demand` — one row per event: demand ratio, sellout verdict +
    date, resale multiplier + price at show time;
  - `synth_resale_series` — resale price trajectory over days-to-show
    checkpoints (45 → 0).

**Provenance on every row:** `synth_run_id`, `generator_version`, `seed`, plus
labeled fallbacks — `capacity_source='tier_estimate'` where no researched
capacity exists, `genre_median` face price where the event was never priced.
Real capacities come from the curated
[`../reference/venue_capacities.csv`](../reference/README.md) (hand-researched
with source URLs — capacity is knowable, so it is never synthesized).

**Determinism:** the base `--seed` (default 683) plus a per-event
SHA-256-derived RNG stream means the same seed reproduces the identical world,
regardless of row order or partial reruns.

## Run

```bash
conda activate music-demand
gcloud auth application-default login

python synth/generate.py --dry-run      # simulate + report, write nothing
python synth/generate.py                # generate + load event_demand_synth
python eda/synth_review.py              # QC: real-vs-synth distribution report
```

Offline tests: `tests/test_synth_heuristics.py`, `tests/test_synth_generate.py`.
The 50x benchmark twin (`fact_event_demand_50x`) is built separately by
`eda/benchmark_partitioning.py --setup`. Honest-vs-derived table rules:
[`../docs/data-model.md`](../docs/data-model.md).
