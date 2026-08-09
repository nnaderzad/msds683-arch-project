"""Offline tests for the committed-docs endpoints behind the "How it works" page."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import repo_docs
from api.app import app
from api.gold import GoldFrames, GoldRepository, set_repository


@pytest.fixture(autouse=True)
def offline_repo():
    empty = pd.DataFrame()
    set_repository(GoldRepository(GoldFrames(empty, empty, empty, empty, empty)))
    yield
    set_repository(None)


@pytest.fixture
def client():
    return TestClient(app)


def test_catalog_lists_only_existing_docs(client):
    docs = client.get("/repo-docs").json()
    names = [doc["name"] for doc in docs]
    assert "readme" in names and "data-model" in names
    assert all({"name", "title", "description"} <= set(doc) for doc in docs)
    # catalog order is preserved for the UI picker
    assert names == [n for n in repo_docs.DOC_CATALOG if n in names]


def test_read_doc_returns_markdown(client):
    doc = client.get("/repo-doc/data-model").json()
    assert doc["name"] == "data-model"
    assert doc["title"] == "Data model"
    assert doc["markdown"].lstrip().startswith("# Data model")


def test_unknown_doc_404s(client):
    assert client.get("/repo-doc/nope").status_code == 404


def test_path_like_names_are_not_paths(client):
    # names are catalog keys, never filesystem paths — traversal has no surface
    assert client.get("/repo-doc/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert repo_docs.read_doc("../README.md") is None


def test_local_only_docs_are_never_in_the_catalog():
    served = {entry["path"] for entry in repo_docs.DOC_CATALOG.values()}
    for private in (
        "docs/PROJECT_STRATEGY.md",
        "docs/team_messages.md",
        "docs/handoff-jun-14.md",
        "docs/collection_cadence_plan.md",
    ):
        assert private not in served
