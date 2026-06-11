"""Shared test setup for the Cloud Function unit tests.

The function's runtime dependencies (functions-framework,
google-cloud-storage) are installed in the Cloud Functions runtime but not
necessarily in the local dev environment. Stub them when missing so the unit
tests run anywhere, then make cloud_functions/ticketmaster_daily importable.

All network and GCS calls are faked in the tests themselves — no test ever
hits the Ticketmaster API or a real bucket.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

FUNCTION_DIR = (
    Path(__file__).resolve().parent.parent / "cloud_functions" / "ticketmaster_daily"
)


def _ensure_stub_modules() -> None:
    try:
        import functions_framework  # noqa: F401
    except ImportError:
        stub = types.ModuleType("functions_framework")
        stub.http = lambda fn: fn  # the decorator just returns the handler
        sys.modules["functions_framework"] = stub

    try:
        from google.cloud import bigquery, storage  # noqa: F401
    except ImportError:
        google_pkg = sys.modules.setdefault("google", types.ModuleType("google"))
        cloud_pkg = types.ModuleType("google.cloud")

        class _StubClient:  # tests must monkeypatch main.<mod>.Client
            def __init__(self, *args, **kwargs):
                raise RuntimeError(
                    "google-cloud SDK is stubbed in tests; "
                    "monkeypatch main.storage.Client / main.bigquery.Client."
                )

        storage_mod = types.ModuleType("google.cloud.storage")
        storage_mod.Client = _StubClient

        class _SchemaField:
            def __init__(self, name, field_type):
                self.name = name
                self.field_type = field_type

        class _LoadJobConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        bigquery_mod = types.ModuleType("google.cloud.bigquery")
        bigquery_mod.Client = _StubClient
        bigquery_mod.SchemaField = _SchemaField
        bigquery_mod.LoadJobConfig = _LoadJobConfig

        cloud_pkg.storage = storage_mod
        cloud_pkg.bigquery = bigquery_mod
        google_pkg.cloud = cloud_pkg
        sys.modules["google.cloud"] = cloud_pkg
        sys.modules["google.cloud.storage"] = storage_mod
        sys.modules["google.cloud.bigquery"] = bigquery_mod


_ensure_stub_modules()
sys.path.insert(0, str(FUNCTION_DIR))

import main  # noqa: E402  (importable only after the stubs above)


@pytest.fixture
def tm_main():
    """The Cloud Function module under test."""
    return main


@pytest.fixture
def base_env(monkeypatch):
    """Minimal env vars the function expects at runtime."""
    monkeypatch.setenv("TICKETMASTER_API_KEY", "test-key")
    monkeypatch.setenv("GCP_PROJECT", "test-project")
    monkeypatch.setenv("GCS_RAW_BUCKET", "test-project-raw")
    monkeypatch.setenv("GCS_PROCESSED_BUCKET", "test-project-processed")
    monkeypatch.setenv("BQ_DATASET", "test_dataset")
