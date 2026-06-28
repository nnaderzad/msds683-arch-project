# Great Expectations — data-quality gates (C1–C4)

Declarative, documented data-quality checks on every medallion layer, run as a CI gate
(offline against the seed) and able to validate the live BigQuery/GCS warehouse via ADC.
The **C1** scaffold (`gx_project.py`) provides the shared context + datasources; the
per-layer suites build on it — **all implemented** (15 checkpoints total):

| Task | Layer | Checks | Status |
|---|---|---|---|
| **C2** | bronze | raw-JSON landing shape (required keys, value ranges, non-empty) — one suite per source (`bronze_suites.py`) | ✅ done |
| **C3** | silver | schema, nulls, value ranges (`interest` ∈ [0,100]), join-key integrity — one suite per fact/dim (`silver_suites.py`) | ✅ done |
| **C4** | gold | `fact_event_demand` integrity — no-row-drop vs spine, unique grain, non-negative price, `days_to_show` monotone, FK (`gold_suites.py`) | ✅ done |

## Why it's built this way (Q&A defense)

- **Great Expectations, all three layers.** Declarative checks with a shared,
  human-readable data-docs artifact (and bonus points). *Rejected:* pandera/pydantic
  (code-level, no shared report); hand-rolled `assert`s (no artifact, no lineage).
- **GX 1.x, driven from Python — not the legacy 0.18 CLI.** The `great_expectations`
  CLI (`init`, `checkpoint list`, …) was **removed in the 1.x line**. We pin the
  current stable `great-expectations==1.18.2` and define datasources / suites /
  checkpoints in code (`gx_project.py`), run via `run_checkpoints.py`. Config-as-code
  is deterministic, reviewable, and re-runnable — the same discipline as the rest of
  the repo. *(This is the one deviation from C1's done-when, which named the 0.18 CLI
  command; `run_checkpoints.py --list` is the 1.x equivalent.)*
- **Offline-first, with the warehouse wired but guarded.** CI validates the committed
  **seed fixtures** (`tests/fixtures/seed/`) through a pandas datasource — no creds,
  no network, fully deterministic. The **BigQuery** and **GCS** datasources (for
  validating the live silver/gold warehouse, C3/C4) are wired but only instantiated
  when ADC is present — exactly like `dbt/profiles.yml`. *Rejected:* requiring live
  creds in CI (flaky, leaks secrets).
- **The GX home (`gx/`) is generated, not committed.** `gx_project.py` is the source
  of truth; the file-context stores are a cache (gitignored). *Rejected:* committing
  the generated JSON stores (UUID churn on every run, two sources of truth).

## Layout

```
great_expectations/
  gx_project.py        # context + datasources (seed/BQ/GCS) + suite/checkpoint runners
  bronze_suites.py     # C2: per-source bronze extractors + raw-JSON landing suites
  silver_suites.py     # C3: silver tables (rebuilt from seed) + schema/null/range/FK suites
  gold_suites.py       # C4: gold fact_event_demand (replicated dbt join) + integrity suite
  forecast_suites.py   # D2: forecast_event_price sanity suite (non-negative, valid horizon)
  run_checkpoints.py   # CLI: run all offline checkpoints, or --list checkpoints
  requirements.txt     # great-expectations==1.18.2 (+ BigQuery driver for C3/C4)
  gx/                  # generated GX file-context home (gitignored)
```

The CI gates live in `tests/test_gx_smoke.py` (C1), `tests/test_gx_bronze.py` (C2),
`tests/test_gx_silver.py` (C3), and `tests/test_gx_gold.py` (C4) — all run in the
existing pytest step.

## Run it

```bash
pip install -r great_expectations/requirements.txt

# Run ALL offline checkpoints over the seed — smoke + bronze + silver + gold
# (exit code is the gate):
python great_expectations/run_checkpoints.py

# List every checkpoint (the 1.x `great_expectations checkpoint list`):
python great_expectations/run_checkpoints.py --list

# The CI gates, locally:
pytest tests/test_gx_smoke.py tests/test_gx_bronze.py tests/test_gx_silver.py tests/test_gx_gold.py -q
```

## Extending it (new checks / live-warehouse validation)

C1–C4 are implemented. To add a check, extend the relevant `*_suites.py` builder — it
flows through `run_checkpoints.py` and the tests automatically. To validate the **live**
warehouse instead of the seed, register the BigQuery/GCS datasource
(`gx_project.register_bigquery_datasource` / `register_gcs_datasource`, ADC) and point the
suite at it; keep the offline seed-backed path so the check stays CI-testable.

> **Not yet automated on GCP.** These suites run in CI and locally; wiring
> `run_checkpoints.py` into the pipeline as a pre-load gate (e.g. the dbt/transform
> Cloud Run job) is a separate `G`-series task, not part of C1–C4.

> **dbt-test vs GX division of labor (decide in G3):** dbt tests guard
> in-warehouse model invariants (PK uniqueness, FK `relationships`, accepted values);
> GX owns cross-cutting, documented data-quality with a shared report. Avoid
> duplicating the same check in both.
