# Great Expectations — data-quality gates (C1 scaffold)

Declarative, documented data-quality checks on every medallion layer. This is the
**C1 scaffold**: the shared GX context, datasources, and a trivial smoke suite that
proves the wiring end-to-end and runs green in CI. The real per-layer suites land on
top of it:

| Task | Layer | Checks | Status |
|---|---|---|---|
| **C2** | bronze | raw-JSON landing shape (required keys, value ranges, non-empty) — one suite per source (`bronze_suites.py`) | ✅ done |
| **C3** | silver | schema, nulls, value ranges (`interest` ∈ [0,100]), join-key integrity — one suite per fact/dim (`silver_suites.py`) | ✅ done |
| **C4** | gold | `fact_event_demand` integrity + forecast sanity | to build |

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
  run_checkpoints.py   # CLI: run all offline checkpoints, or --list checkpoints
  requirements.txt     # great-expectations==1.18.2 (+ BigQuery driver for C3/C4)
  gx/                  # generated GX file-context home (gitignored)
```

The CI gates live in `tests/test_gx_smoke.py` (C1), `tests/test_gx_bronze.py` (C2),
and `tests/test_gx_silver.py` (C3) — all run in the existing pytest step.

## Run it

```bash
pip install -r great_expectations/requirements.txt

# Run the offline smoke checkpoint over the seed (exit code is the gate):
python great_expectations/run_checkpoints.py

# List checkpoints (the 1.x `great_expectations checkpoint list`):
python great_expectations/run_checkpoints.py --list

# The CI gate, locally:
pytest tests/test_gx_smoke.py -q
```

## Extending it (C2 / C3 / C4)

Add a layer suite in `gx_project.py` (mirror `build_smoke_suite`), wire a checkpoint
(mirror `build_smoke_checkpoint`), and register it in `run_checkpoints.py`. Validate
bronze through the GCS datasource, silver/gold through the BigQuery datasource (both
already wired); keep an offline seed-backed path so the suite stays CI-testable.

> **dbt-test vs GX division of labor (decide in G3):** dbt tests guard
> in-warehouse model invariants (PK uniqueness, FK `relationships`, accepted values);
> GX owns cross-cutting, documented data-quality with a shared report. Avoid
> duplicating the same check in both.
