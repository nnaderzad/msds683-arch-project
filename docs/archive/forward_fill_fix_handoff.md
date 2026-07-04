# Handoff — TM price forward-fill honesty fix

> Working note for a fresh session. **Uncommitted/untracked** by design — commit it on the
> fix branch if you want it shared, or delete it when done. Task #9.

## The bug (what we're fixing)

`fact_ticketmaster` (the event × snapshot_date price **history** the model trains on) is
sourced from the processed parquet, which is an **EXPORT of current-state `tm_events`** —
a table that MERGE-upserts and **never deletes**. So once an event is first seen, it is
re-emitted into **every** later daily snapshot carrying its **last-known price**, even on
days the sweep never re-observed it.

➡️ The "daily price history" is **last-known price forward-filled**, not true daily
observations. It **manufactures data and overstates coverage**, and the forecast's
"price vs days_to_show" is partly learning from synthetic flat segments.

**Proof** (already committed): `eda/diagnose_price_gaps.py`, run against live BQ, found
**0 interior gaps and 0 trailing gaps** across 9,661 priced events — structurally
impossible unless events never leave the panel (i.e. forward-fill). Output in
`eda/output/tm_price_gap_diagnosis*`.

## Goal

A silver `(event, snapshot_date)` price row should exist **only for days the event was
actually OBSERVED** by the sweep, with the price **as observed that day** (NULL if observed
without a price). **No forward-fill in silver.** If gold fills gaps for chart/UI continuity,
it must be **explicitly labeled** (e.g. a `price_is_filled` BOOL) and documented as
**team-derived/aggregated** — never presented as raw source data.

## Where the forward-fill happens (root-cause files)

- `cloud_functions/ticketmaster_daily/main.py`:
  - `upsert_to_silver()` — MERGE into current-state `tm_events` (never deletes).
  - `export_to_processed()` — `EXPORT DATA ... AS SELECT * FROM tm_events` (overwrite) →
    the daily parquet = the whole current-state table, **forward-filled**.
  - Reusable: `flatten_event()` (raw event → staging row shape) and `first_price_range()`
    (picks the embedded `standard` price) — **reuse these to parse bronze**.
- `dbt/models/silver/fact_ticketmaster.sql` — reads the **processed parquet** (the
  forward-filled export) via the dbt external source `tm_snapshots_ext`, `snapshot_date`
  from `_FILE_NAME`. **This is what must change** to read honest observations.

## The honest source = BRONZE (verified shape)

`gs://<proj>-raw/ticketmaster/dt=<YYYY-MM-DD>/ticketmaster_<STATE>_<stamp>.json`
- Each file is a **JSON array of event objects** (NOT NDJSON, NOT the `_embedded` envelope —
  the collector already flattened pages into one array). Each event has `id`, `name`,
  `dates`, `priceRanges` (embedded), venue under `_embedded`, etc.
- **6 files per state per day** (the scheduler runs 6×/day; each run lands its own stamped
  file). An event is **observed on `dt=D`** iff it appears in **any** of that day's files.
- ~16 days of bronze so far (dt=2026-06-13 onward).

So the honest panel is directly buildable: for each `(event_id, dt)`, the event was
observed that day iff present in one of `dt`'s files; price = as observed (NULL allowed);
**no carry-forward**.

## Recommended approach (refine in planning)

Build an honest `fact_ticketmaster` from bronze, **alongside** the current one
(create → verify → switch; don't drop the working table first):

1. **Bronze→observation loader.** A BQ external table over JSON *arrays* is awkward, so a
   **Python bronze loader** (mirror `pipeline/silver/trends_series_to_silver.py` +
   `trends_to_silver.py`: parallel threaded read, pure flatten, staging+MERGE) is the
   pragmatic path — OR a land-NDJSON-then-dbt step. Reuse `flatten_event()` /
   `first_price_range()` from `main.py` and `common/keys.py` for surrogate ids.
   - `snapshot_date = the file's dt`; one row per `(event_id, snapshot_date)`.
   - **Within-day dedup across the 6 runs** — decide the rule (e.g. last run of the day
     wins, or "priced if any run priced"). Make it deterministic.
   - Price = observed value that day (NULL if the event appeared without `priceRanges`).
2. **Point gold at the honest fact.** `dbt/models/gold/fact_event_demand.sql` joins the
   `fact_ticketmaster` spine — gold row counts will **drop** to real observations (expected
   and correct). Re-`dbt build`, then **re-materialize** `forecast_event_price`
   (`python pipeline/gold/export_predictions_table.py`).
3. **Optional gold fill (labeled).** If the demo chart needs continuity, forward-fill *in
   gold only* with an explicit `price_is_filled` flag + a docstring/`docs/` note that it's
   team-derived. Default to NOT filling unless the UI demands it.

## Downstream impacts to check

- **GX silver/gold suites** (`great_expectations/silver_suites.py`, `gold_suites.py`) —
  the gold "no-row-drop" test compares gold rows == spine (still holds; gold mirrors the
  new honest spine). Ensure no suite assumes forward-filled completeness / coverage_span.
- **Model** (`model/features.py` keeps priced pre-show rows) — fewer, real rows; the
  pooled cross-sectional design still holds. Re-run D2 export after.
- **`tm_events` itself can stay** as current-state for the **dims** (A3) — only the price
  **history fact** changes source. (Or prune past shows there separately — out of scope.)
- The **`eda/tm_price_eda.py`** completeness numbers (H1) will drop to honest values.

## Verification (deterministic)

- **Re-run `eda/diagnose_price_gaps.py`** against the honest fact → it should now show
  **real interior/trailing gaps > 0** where the sweep genuinely missed events. That's the
  proof forward-fill is gone (today it's 0/0). Diff `eda/output/tm_price_gap_diagnosis*`.
- Honest `price_completeness` per event drops vs the forward-filled numbers — expected.
- PK unique on `(event_id, snapshot_date)`; idempotent re-run; GX suites pass; gold row
  count == new spine; `forecast_event_price` re-materializes.

## Constraints / conventions

- Deterministic, committed `.py`, no runtime LLM; reuse `common/keys.py`, the staging+MERGE
  pattern, and `flatten_event()`/`first_price_range()` — don't reinvent.
- Create→verify→delete: build the honest fact, verify, then switch gold; don't overwrite
  the working table first. Many small commits. No AI attribution. Never commit the TM key.
- Branch off latest `main` (PR #30 = the COLLECT-1 Trends work should be merged first, or
  rebase — it doesn't touch `fact_ticketmaster`, so no real conflict).

## Pointers

- Memory: `tm-diagnostic-forwardfill-trends-pivot` (the diagnostic + this bug + COLLECT-1).
- `team-plan.md` COLLECT-1 → "Follow-ons" lists this as the forward-fill honesty fix.
- The diagnostic + its committed output are the before/after baseline.
