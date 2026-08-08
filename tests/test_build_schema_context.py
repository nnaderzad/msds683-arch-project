"""Offline tests for the schema-context generator's pure rendering logic.

No BigQuery, no network: ``render_context`` is exercised with fake
INFORMATION_SCHEMA rows. The live pieces (INFORMATION_SCHEMA fetch, few-shot
dry-run) are exercised by actually running the generator against the warehouse.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "eda"))
sys.path.insert(0, str(REPO_ROOT))

bsc = importlib.import_module("build_schema_context")
from api.text2sql import ALLOWED_TABLES  # noqa: E402

AS_OF = "2026-08-08T00:00:00+00:00"


def fake_columns() -> list[dict[str, str]]:
    rows = []
    for table in sorted(ALLOWED_TABLES):
        rows.append({"table_name": table, "column_name": "event_id", "data_type": "STRING"})
        if table.startswith("fact_"):
            rows.append(
                {"table_name": table, "column_name": "snapshot_date", "data_type": "DATE"}
            )
    return rows


def fake_stats() -> dict[str, dict[str, str]]:
    return {t: {"row_count": "42", "latest": "2026-08-08"} for t in sorted(ALLOWED_TABLES)}


def render() -> str:
    return bsc.render_context(fake_columns(), fake_stats(), "proj", "ds", AS_OF)


def test_renders_every_allowed_table_and_only_those():
    text = render()
    for table in ALLOWED_TABLES:
        assert f"### {table}" in text
    assert "fact_event_demand_continuous" not in text
    assert "tm_events" not in text


def test_semantic_rules_and_joins_present():
    text = render()
    assert "NEVER compare, rank, or" in text  # trends normalization rule
    assert "show_date = the concert day" in text
    assert "OBSERVED-ONLY" in text
    assert "Join map" in text


def test_few_shots_render_with_dataset_qualification():
    text = render()
    assert "`proj.ds`.fact_event_demand" in text
    assert text.count("```sql") == len(bsc.FEW_SHOTS)


def test_generation_stamp_and_budget():
    text = render()
    assert AS_OF in text
    assert len(text) <= bsc.MAX_CHARS


def test_missing_table_fails():
    columns = [r for r in fake_columns() if r["table_name"] != "dim_geo"]
    with pytest.raises(SystemExit, match="dim_geo"):
        bsc.render_context(columns, fake_stats(), "proj", "ds", AS_OF)


def test_budget_overflow_fails(monkeypatch):
    monkeypatch.setattr(bsc, "MAX_CHARS", 100)
    with pytest.raises(SystemExit, match="chars"):
        render()


def test_every_allowed_table_has_curated_note():
    assert ALLOWED_TABLES <= bsc.TABLE_NOTES.keys()
