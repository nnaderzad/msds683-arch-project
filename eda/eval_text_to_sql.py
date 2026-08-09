#!/usr/bin/env python3
"""Empirical evaluation of the text-to-SQL agent against the committed question set.

Runs every question in ``eda/text2sql_eval_set.yaml`` through the REAL agent
(``api.text2sql``: Gemini on Vertex + guardrails + BigQuery) and scores each
answer by **execution-result match**: the agent's rows must equal the committed
gold SQL's rows (unordered multiset, values-only, floats rounded) — many SQL
strings are correct, so string-matching SQL would under-count. ``refusal``
questions pass when the agent refuses or blocks.

Gold SQL executes live at eval time, so expected values track the moving
warehouse; the question set, scoring code, and report are committed, making the
evaluation re-runnable and diffable (repo convention — see ``eda/_common.py``).
The LLM call is the sanctioned runtime exception for this demo feature; scoring
itself is deterministic and offline-tested in ``tests/test_eval_text_to_sql.py``.

Run (repo root, ADC authed, Vertex AI enabled):

    python eda/eval_text_to_sql.py --runs 3

Outputs:
  * eda/output/text_to_sql_eval.md   — accuracy by tier + failure taxonomy
  * eda/output/text_to_sql_eval.csv  — one row per question x run
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
sys.path.insert(0, str(REPO_ROOT))
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, utc_now_iso  # noqa: E402

OUT_DIR = REPO_ROOT / "eda" / "output"
QUESTIONS_PATH = REPO_ROOT / "eda" / "text2sql_eval_set.yaml"
TIER_ORDER = ["easy", "join", "aggregate", "trick"]
REFUSED_STATUSES = {"refused", "blocked"}


@dataclass(frozen=True)
class Question:
    id: str
    tier: str
    category: str
    question: str
    expect: dict[str, Any]


@dataclass
class QuestionRun:
    question: Question
    run: int
    result: dict[str, Any]
    passed: bool
    failure_class: str


# ---------------------------------------------------------------------------
# Pure scoring logic (offline-tested)
# ---------------------------------------------------------------------------


def canon_value(value: Any) -> str:
    """Canonicalize one cell for comparison: floats to 2dp, everything else str."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    text = str(value).strip()
    try:
        return f"{float(text):.2f}"
    except ValueError:
        return text.lower()


def canonicalize(rows: list[dict[str, Any]]) -> Counter:
    """Rows -> unordered multiset of value-tuples (column names/order ignored)."""
    return Counter(tuple(sorted(canon_value(v) for v in row.values())) for row in rows)


def scalar_of(rows: list[dict[str, Any]]) -> Any:
    if not rows:
        return None
    return next(iter(rows[0].values()), None)


def score(gold_rows: list[dict[str, Any]] | None, result: dict[str, Any],
          expect: dict[str, Any]) -> bool:
    """Did the agent's response satisfy the expectation?"""
    kind = expect["type"]
    if kind == "refusal":
        return result.get("status") in REFUSED_STATUSES
    if result.get("status") != "ok":
        return False
    rows = result.get("rows") or []
    if kind == "answers":
        # Open-ended analytical questions with many defensible SQL shapes: the
        # graded contract is only "must produce a non-empty answer, not refuse" —
        # the inverse of a refusal probe. Result quality is reviewed by hand
        # (eda/user_test_log.yaml), never string-matched.
        return bool(rows)
    if kind == "scalar":
        got, want = scalar_of(rows), scalar_of(gold_rows or [])
        tol = float(expect.get("tol", 0.0))
        try:
            return abs(float(got) - float(want)) <= tol
        except (TypeError, ValueError):
            return canon_value(got) == canon_value(want)
    if kind == "rows":
        return canonicalize(rows) == canonicalize(gold_rows or [])
    raise ValueError(f"unknown expect.type: {kind}")


def classify_failure(expect: dict[str, Any], result: dict[str, Any], passed: bool) -> str:
    """Bucket a failed run for the report's failure-taxonomy section."""
    if passed:
        return ""
    status = result.get("status")
    if expect["type"] == "refusal":
        return "missed_refusal"  # answered something it should have refused
    if status == "refused":
        return "refused_wrongly"
    if status == "blocked":
        return "blocked_wrongly"
    if status != "ok":
        return "execution_error"
    return "result_mismatch"


# ---------------------------------------------------------------------------
# Loading / running
# ---------------------------------------------------------------------------


def load_questions(path: Path, project: str, dataset: str) -> list[Question]:
    import yaml

    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    ds = f"`{project}.{dataset}"
    questions = []
    for entry in entries:
        expect = dict(entry["expect"])
        if "gold_sql" in expect:
            expect["gold_sql"] = expect["gold_sql"].format(ds=ds)
        questions.append(
            Question(entry["id"], entry["tier"], entry["category"], entry["question"], expect)
        )
    return questions


def run_eval(service, gold_runner, questions: list[Question], runs: int) -> list[QuestionRun]:
    gold_cache: dict[str, list[dict[str, Any]]] = {}
    results: list[QuestionRun] = []
    for question in questions:
        gold_rows = None
        if "gold_sql" in question.expect:
            if question.id not in gold_cache:
                gold_cache[question.id] = gold_runner.run(question.expect["gold_sql"]).rows
            gold_rows = gold_cache[question.id]
        for run in range(1, runs + 1):
            result = service.ask(question.question)
            passed = score(gold_rows, result, question.expect)
            failure = classify_failure(question.expect, result, passed)
            results.append(QuestionRun(question, run, result, passed, failure))
            marker = "PASS" if passed else f"FAIL({failure})"
            print(f"[eval] {question.id} run {run}/{runs}: {marker}")
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _accuracy(rows: list[QuestionRun]) -> str:
    if not rows:
        return "—"
    return f"{sum(r.passed for r in rows) / len(rows) * 100:.0f}%"


def write_report(results: list[QuestionRun], out_dir: Path, as_of: str, model: str,
                 runs: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "text_to_sql_eval.md"
    lines = [
        "# Text-to-SQL agent — evaluation report",
        "",
        f"Generated {as_of} by `eda/eval_text_to_sql.py --runs {runs}` (model: {model}).",
        "Scoring: execution-result match against committed gold SQL (values-only multiset;",
        "refusal questions pass on refused/blocked). Re-run the same command to refresh.",
        "",
        "## Accuracy",
        "",
        "| Tier | Questions | Runs | Accuracy |",
        "|---|---|---|---|",
    ]
    for tier in TIER_ORDER:
        tier_rows = [r for r in results if r.question.tier == tier]
        n_questions = len({r.question.id for r in tier_rows})
        lines.append(f"| {tier} | {n_questions} | {len(tier_rows)} | {_accuracy(tier_rows)} |")
    lines.append(
        f"| **overall** | {len({r.question.id for r in results})} | {len(results)} "
        f"| **{_accuracy(results)}** |"
    )

    lines += ["", "## Per-question results", "",
              "| id | tier | pass | status(es) | failure | est. bytes |", "|---|---|---|---|---|---|"]
    by_question: dict[str, list[QuestionRun]] = {}
    for row in results:
        by_question.setdefault(row.question.id, []).append(row)
    for qid, rows in by_question.items():
        statuses = ",".join(dict.fromkeys(r.result.get("status", "?") for r in rows))
        failures = ",".join(sorted({r.failure_class for r in rows if r.failure_class})) or "—"
        max_bytes = max((r.result.get("bytes_processed") or 0) for r in rows)
        lines.append(
            f"| {qid} | {rows[0].question.tier} | {sum(r.passed for r in rows)}/{len(rows)} "
            f"| {statuses} | {failures} | {max_bytes:,} |"
        )

    lines += ["", "## Where it fails and why", ""]
    failures = [r for r in results if not r.passed]
    if not failures:
        lines.append("No failures in this run.")
    by_class: dict[str, list[QuestionRun]] = {}
    for row in failures:
        by_class.setdefault(row.failure_class, []).append(row)
    for failure_class, rows in sorted(by_class.items()):
        lines += [f"### {failure_class} ({len(rows)} run(s))", ""]
        seen: set[str] = set()
        for row in rows:
            if row.question.id in seen:
                continue
            seen.add(row.question.id)
            lines.append(f"- **{row.question.id}** — “{row.question.question}”")
            sql = (row.result.get("sql") or "").replace("\n", " ")
            if sql:
                lines.append(f"  - SQL: `{sql[:220]}`")
            answer = (row.result.get("answer") or "").replace("\n", " ")
            if answer:
                lines.append(f"  - agent said: {answer[:220]}")
        lines.append("")

    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with (out_dir / "text_to_sql_eval.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "tier", "category", "run", "passed", "status", "failure_class",
                         "bytes_processed", "latency_ms"])
        for row in results:
            writer.writerow([
                row.question.id, row.question.tier, row.question.category, row.run,
                int(row.passed), row.result.get("status"), row.failure_class,
                row.result.get("bytes_processed"), row.result.get("latency_ms"),
            ])
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--ids", nargs="*", help="run only these question ids (iteration aid)")
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    from api.text2sql import BigQueryRunner, get_service

    questions = load_questions(args.questions, args.project, args.dataset)
    if args.ids:
        questions = [q for q in questions if q.id in set(args.ids)]
    service = get_service()
    results = run_eval(service, BigQueryRunner(args.project), questions, args.runs)
    report = write_report(results, args.output_dir, utc_now_iso(), service.model, args.runs)
    overall = _accuracy(results)
    print(f"[eval] overall accuracy {overall} across {len(results)} runs -> {report}")


if __name__ == "__main__":
    main()
