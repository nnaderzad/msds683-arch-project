"""Great Expectations scaffold (task C1) — shared context, datasources, suites.

This is the foundation the per-layer suites build on:
  * C2 — bronze raw-JSON landing checks
  * C3 — silver schema / null / value-range / join-key checks
  * C4 — gold `fact_event_demand` integrity + forecast sanity

Design choices (defend these in Q&A):
  * **GX 1.x, driven from Python — not the 0.18 CLI.** The `great_expectations`
    CLI (`checkpoint list`, `init`, ...) was removed in the 1.x line, so we define
    datasources / suites / checkpoints in code and run them with
    ``run_checkpoints.py``. Code-defined config is deterministic, reviewable, and
    re-runnable — the same discipline the rest of this repo follows.
  * **Two datasource families.** An offline *pandas* datasource over the committed
    seed fixtures (``tests/fixtures/seed/``) is what CI exercises — no creds, no
    network, fully deterministic. A *BigQuery* (and *GCS*) datasource is wired for
    validating the live warehouse locally with ADC; both are guarded so they are
    only instantiated when credentials are present (mirrors ``dbt/profiles.yml``).
  * **Idempotent.** Every builder is safe to re-run; re-running ``run_checkpoints``
    is a no-op against the committed project.

Auth (BigQuery/GCS paths only): Application Default Credentials —
``gcloud auth application-default login`` once. CI stays on the offline path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

import great_expectations as gx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# This file lives in <repo>/great_expectations/. The GX *file context* home is
# <repo>/great_expectations/gx/ (created by gx.get_context); the repo root is one
# level up and anchors the seed-fixture path.
GX_DIR = Path(__file__).resolve().parent
REPO_ROOT = GX_DIR.parent
SEED_TM_CSV = REPO_ROOT / "tests" / "fixtures" / "seed" / "ticketmaster" / "tm_events_seed.csv"

# Live-warehouse defaults, env-overridable exactly like dbt/profiles.yml.
BQ_PROJECT = os.environ.get("DBT_GCP_PROJECT", "data-architecture-498123")
BQ_DATASET = os.environ.get("DBT_BQ_DATASET", "event_demand_analytics")

# Stable resource names so listing/re-runs are deterministic.
SEED_DATASOURCE = "seed_pandas"
SEED_ASSET = "tm_events_seed"
SEED_BATCH = "whole_table"
SMOKE_SUITE = "bronze_seed_smoke"
SMOKE_VALIDATION = "bronze_seed_smoke_validation"
SMOKE_CHECKPOINT = "bronze_seed_smoke_checkpoint"


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def get_context(project_root: str | os.PathLike | None = None):
    """Return a file-backed GX context.

    Defaults to the committed project at ``great_expectations/gx``; tests pass a
    temp ``project_root`` so they never write into the committed home.
    """
    root = Path(project_root) if project_root is not None else GX_DIR
    return gx.get_context(mode="file", project_root_dir=str(root))


# ---------------------------------------------------------------------------
# Datasources
# ---------------------------------------------------------------------------

def register_seed_datasource(context):
    """Offline pandas datasource over the seed fixtures → a whole-table batch.

    This is the CI substrate: no creds, no network. Returns the batch definition
    the smoke suite validates against.
    """
    try:
        ds = context.data_sources.add_pandas(name=SEED_DATASOURCE)
    except Exception:
        ds = context.data_sources.get(SEED_DATASOURCE)
    try:
        asset = ds.add_dataframe_asset(name=SEED_ASSET)
    except Exception:
        asset = ds.get_asset(SEED_ASSET)
    try:
        return asset.add_batch_definition_whole_dataframe(SEED_BATCH)
    except Exception:
        return asset.get_batch_definition(SEED_BATCH)


def register_bigquery_datasource(context, *, project: str = BQ_PROJECT, dataset: str = BQ_DATASET):
    """Wire a BigQuery datasource for live silver/gold validation (C3/C4).

    Guarded: the driver is imported lazily and the datasource is only created on
    demand (ADC required), so importing this module never forces a BQ dependency
    or a credential check. Not used by the offline CI path.
    """
    import sqlalchemy_bigquery  # noqa: F401  # fail fast with a clear error if missing

    connection_string = f"bigquery://{project}/{dataset}"
    try:
        return context.data_sources.add_sql(name="warehouse_bigquery", connection_string=connection_string)
    except Exception:
        return context.data_sources.get("warehouse_bigquery")


def register_gcs_datasource(context, *, bucket: str | None = None):
    """Wire a pandas-on-GCS datasource for bronze raw-JSON validation (C2).

    Guarded like the BigQuery one — only call it with ADC + a bucket. Defaults to
    the project bronze bucket.
    """
    bucket = bucket or f"{BQ_PROJECT}-raw"
    try:
        return context.data_sources.add_pandas_gcs(name="bronze_gcs", bucket_or_name=bucket)
    except Exception:
        return context.data_sources.get("bronze_gcs")


# ---------------------------------------------------------------------------
# Suite + checkpoint (the trivial scaffold gate; C2–C4 add real suites)
# ---------------------------------------------------------------------------

def build_smoke_suite(context):
    """A deliberately trivial suite proving the wiring end-to-end.

    Anchored on real seed columns so it is meaningful, not a tautology: the seed
    Ticketmaster events must have a non-null ``event_id`` and at least one row.
    """
    suite = gx.ExpectationSuite(name=SMOKE_SUITE)
    suite.add_expectation(gx.expectations.ExpectColumnToExist(column="event_id"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="event_id"))
    suite.add_expectation(gx.expectations.ExpectTableRowCountToBeBetween(min_value=1, max_value=1_000_000))
    return context.suites.add_or_update(suite)


def _add_or_replace(factory, obj, name):
    """add() the object, replacing any existing one of the same name (idempotent)."""
    try:
        return factory.add(obj)
    except Exception:
        factory.delete(name)
        return factory.add(obj)


def build_smoke_checkpoint(context, batch_definition, suite):
    """Wire the batch + suite into a validation definition + checkpoint."""
    validation = gx.ValidationDefinition(
        name=SMOKE_VALIDATION, data=batch_definition, suite=suite
    )
    validation = _add_or_replace(context.validation_definitions, validation, SMOKE_VALIDATION)

    checkpoint = gx.Checkpoint(name=SMOKE_CHECKPOINT, validation_definitions=[validation])
    return _add_or_replace(context.checkpoints, checkpoint, SMOKE_CHECKPOINT)


def list_checkpoints(context) -> list[str]:
    """Names of every checkpoint in the project (the 1.x `checkpoint list`)."""
    return [cp.name for cp in context.checkpoints.all()]


def run_suite_on_dataframe(context, df, suite, *, key: str):
    """Validate an in-memory dataframe against ``suite`` and return the result.

    Generalizes the smoke flow so any layer (C2 bronze, C3 silver, …) can run a
    suite over a dataframe with one call. ``key`` namespaces the per-run GX
    resources (datasource / asset / batch / validation / checkpoint) so several
    suites coexist in one context. Idempotent.
    """
    ds_name, asset_name, batch_name = f"{key}_pandas", f"{key}_asset", f"{key}_batch"
    validation_name, checkpoint_name = f"{key}_validation", f"{key}_checkpoint"

    try:
        ds = context.data_sources.add_pandas(name=ds_name)
    except Exception:
        ds = context.data_sources.get(ds_name)
    try:
        asset = ds.add_dataframe_asset(name=asset_name)
    except Exception:
        asset = ds.get_asset(asset_name)
    try:
        batch = asset.add_batch_definition_whole_dataframe(batch_name)
    except Exception:
        batch = asset.get_batch_definition(batch_name)

    suite = context.suites.add_or_update(suite)
    validation = gx.ValidationDefinition(name=validation_name, data=batch, suite=suite)
    validation = _add_or_replace(context.validation_definitions, validation, validation_name)
    checkpoint = gx.Checkpoint(name=checkpoint_name, validation_definitions=[validation])
    checkpoint = _add_or_replace(context.checkpoints, checkpoint, checkpoint_name)
    return checkpoint.run(batch_parameters={"dataframe": df})


# ---------------------------------------------------------------------------
# Offline run (used by run_checkpoints.py and the CI smoke test)
# ---------------------------------------------------------------------------

def run_offline(project_root: str | os.PathLike | None = None):
    """Build the offline scaffold and run the smoke checkpoint on the seed.

    Returns the GX checkpoint result; ``result.success`` is the CI gate.
    """
    context = get_context(project_root)
    batch_definition = register_seed_datasource(context)
    suite = build_smoke_suite(context)
    checkpoint = build_smoke_checkpoint(context, batch_definition, suite)
    seed_df = pd.read_csv(SEED_TM_CSV)
    return checkpoint.run(batch_parameters={"dataframe": seed_df})
