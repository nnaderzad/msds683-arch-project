"""Offline tests for the eval harness's pure scoring logic (no LLM, no BigQuery)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "eda"))
sys.path.insert(0, str(REPO_ROOT))

ev = importlib.import_module("eval_text_to_sql")


def ok(rows, **extra):
    return {"status": "ok", "rows": rows, **extra}


# ---------------------------------------------------------------------------
# canonicalize / scalar comparison
# ---------------------------------------------------------------------------


def test_row_order_and_column_names_ignored():
    got = [{"a": 1, "b": "X"}, {"a": 2, "b": "Y"}]
    gold = [{"count": 2, "label": "y"}, {"count": 1, "label": "x"}]
    assert ev.canonicalize(got) == ev.canonicalize(gold)


def test_float_rounding_and_string_numbers_match():
    assert ev.canonicalize([{"p": 42.004}]) == ev.canonicalize([{"price": "42.0"}])


def test_multiset_duplicates_matter():
    assert ev.canonicalize([{"a": 1}, {"a": 1}]) != ev.canonicalize([{"a": 1}])


def test_scalar_tolerance():
    expect = {"type": "scalar", "tol": 0.01}
    assert ev.score([{"m": 136.05}], ok([{"min_price": 136.054}]), expect)
    assert not ev.score([{"m": 136.05}], ok([{"min_price": 137.0}]), expect)


def test_scalar_string_equality():
    expect = {"type": "scalar"}
    assert ev.score([{"s": "CA"}], ok([{"state": "ca"}]), expect)


def test_scalar_date_values_compare_as_strings():
    expect = {"type": "scalar"}
    assert ev.score([{"d": "2026-10-24"}], ok([{"next_show": "2026-10-24"}]), expect)
    assert not ev.score([{"d": "2026-10-24"}], ok([{"next_show": "2026-10-25"}]), expect)


# ---------------------------------------------------------------------------
# score / refusal / failure taxonomy
# ---------------------------------------------------------------------------


def test_refusal_passes_on_refused_or_blocked():
    expect = {"type": "refusal"}
    assert ev.score(None, {"status": "refused"}, expect)
    assert ev.score(None, {"status": "blocked"}, expect)
    assert not ev.score(None, ok([{"a": 1}]), expect)


def test_non_ok_status_fails_answer_questions():
    expect = {"type": "scalar"}
    assert not ev.score([{"a": 1}], {"status": "error", "rows": []}, expect)


def test_rows_match():
    expect = {"type": "rows"}
    gold = [{"genre": "Rock"}, {"genre": "Pop"}]
    assert ev.score(gold, ok([{"g": "pop"}, {"g": "rock"}]), expect)
    assert not ev.score(gold, ok([{"g": "rock"}]), expect)


def test_unknown_expect_type_raises():
    with pytest.raises(ValueError):
        ev.score([], ok([]), {"type": "nope"})


@pytest.mark.parametrize(
    ("expect_type", "status", "expected_class"),
    [
        ("refusal", "ok", "missed_refusal"),
        ("scalar", "refused", "refused_wrongly"),
        ("scalar", "blocked", "blocked_wrongly"),
        ("scalar", "error", "execution_error"),
        ("scalar", "ok", "result_mismatch"),
    ],
)
def test_failure_classes(expect_type, status, expected_class):
    result = {"status": status, "rows": []}
    assert ev.classify_failure({"type": expect_type}, result, passed=False) == expected_class


def test_passed_run_has_empty_failure_class():
    assert ev.classify_failure({"type": "scalar"}, ok([]), passed=True) == ""


# ---------------------------------------------------------------------------
# question loading + report rendering
# ---------------------------------------------------------------------------


def test_load_questions_expands_dataset():
    pytest.importorskip("yaml")
    questions = ev.load_questions(ev.QUESTIONS_PATH, "proj", "ds")
    assert len(questions) >= 20
    tiers = {q.tier for q in questions}
    assert tiers == set(ev.TIER_ORDER)
    gold = next(q for q in questions if "gold_sql" in q.expect)
    assert "`proj.ds." in gold.expect["gold_sql"]
    refusals = [q for q in questions if q.expect["type"] == "refusal"]
    assert len(refusals) >= 3


def test_write_report_renders_accuracy_and_failures(tmp_path):
    question = ev.Question("q1", "easy", "cat", "How many?", {"type": "scalar"})
    runs = [
        ev.QuestionRun(question, 1, ok([{"n": 1}], bytes_processed=10, latency_ms=5), True, ""),
        ev.QuestionRun(
            question, 2,
            {"status": "ok", "rows": [{"n": 2}], "sql": "SELECT 2", "answer": "two"},
            False, "result_mismatch",
        ),
    ]
    md = ev.write_report(runs, tmp_path, "2026-08-08T00:00:00+00:00", "fake-model", 2)
    text = md.read_text(encoding="utf-8")
    assert "| easy | 1 | 2 | 50% |" in text
    assert "result_mismatch" in text
    assert "SELECT 2" in text
    assert (tmp_path / "text_to_sql_eval.csv").exists()
