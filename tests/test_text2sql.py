"""Offline tests for the text-to-SQL guardrail pipeline and the /ask endpoint.

FakeLlm/FakeRunner exercise the full ``Text2SqlService`` flow with zero network,
zero credentials, and no google-genai SDK — the same injection seam the live app
uses (``set_service``), mirroring how tests/test_api.py fakes the gold repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from api import text2sql as t2s
from api.app import app
from api.gold import GoldFrames, GoldRepository, set_repository

DS = "`data-architecture-498123.event_demand_analytics"
GOOD_SQL = f"SELECT event_id FROM {DS}.fact_event_demand` WHERE snapshot_date = '2026-08-08'"


class FakeLlm:
    """Routes prompts like the real model sees them: answer-summarization prompts
    return ``answer``; SQL-generation prompts pop queued responses in order,
    repeating the last one when exhausted (so repeated asks keep working)."""

    def __init__(self, *sql_responses: str, answer: str = "Found 1 event."):
        self.sql_responses = list(sql_responses) or [GOOD_SQL]
        self.answer = answer
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Summarize"):
            return self.answer
        if len(self.sql_responses) > 1:
            return self.sql_responses.pop(0)
        return self.sql_responses[0]


class FakeRunner:
    def __init__(self, rows=None, dry_bytes: int = 1024, fail_dry_times: int = 0):
        self.rows = rows if rows is not None else [{"event_id": "abc"}]
        self.dry_bytes = dry_bytes
        self.fail_dry_times = fail_dry_times
        self.executed: list[str] = []

    def dry_run(self, sql: str) -> int:
        if self.fail_dry_times > 0:
            self.fail_dry_times -= 1
            raise RuntimeError("Unrecognized name: snapshot_dat at [1:20]")
        return self.dry_bytes

    def run(self, sql: str) -> t2s.QueryResult:
        self.executed.append(sql)
        return t2s.QueryResult(rows=self.rows, total_bytes_processed=self.dry_bytes)


def make_service(llm=None, runner=None) -> t2s.Text2SqlService:
    return t2s.Text2SqlService(
        llm=llm or FakeLlm(GOOD_SQL),
        runner=runner or FakeRunner(),
        schema_context="## fake schema context",
        model="fake-model",
    )


@pytest.fixture(autouse=True)
def reset_singletons():
    # An empty in-memory gold repo stops the app lifespan pre-warm from doing a
    # real BigQuery load when TestClient(app) starts (same seam as test_api.py).
    empty = pd.DataFrame()
    set_repository(GoldRepository(GoldFrames(empty, empty, empty, empty, empty)))
    t2s.set_service(None)
    yield
    t2s.set_service(None)
    set_repository(None)


# ---------------------------------------------------------------------------
# validate_sql — the AST gate
# ---------------------------------------------------------------------------


def test_valid_select_passes():
    verdicts = t2s.validate_sql(GOOD_SQL)
    assert all(v.passed for v in verdicts)


def test_drop_table_blocked():
    verdicts = t2s.validate_sql("DROP TABLE fact_event_demand")
    failed = {v.name for v in verdicts if not v.passed}
    assert "select_only" in failed


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO fact_event_demand (event_id) VALUES ('x')",
        "DELETE FROM fact_event_demand WHERE TRUE",
        "UPDATE dim_event SET show_date = NULL WHERE TRUE",
        "MERGE dim_venue t USING dim_venue s ON FALSE WHEN NOT MATCHED THEN INSERT ROW",
        "TRUNCATE TABLE fact_event_demand",
    ],
)
def test_dml_blocked(sql):
    assert not all(v.passed for v in t2s.validate_sql(sql))


def test_multi_statement_blocked():
    two = f"{GOOD_SQL}; DROP TABLE dim_event"
    verdicts = t2s.validate_sql(two)
    assert not all(v.passed for v in verdicts)


def test_disallowed_table_blocked():
    sql = f"SELECT * FROM {DS}.tm_events`"
    failed = [v for v in t2s.validate_sql(sql) if not v.passed]
    assert failed and "tm_events" in failed[0].detail


def test_continuous_table_blocked():
    sql = f"SELECT COUNT(*) FROM {DS}.fact_event_demand_continuous`"
    assert not all(v.passed for v in t2s.validate_sql(sql))


def test_cte_aliases_not_false_flagged():
    sql = (
        f"WITH per_event AS (SELECT event_id FROM {DS}.fact_event_demand`) "
        "SELECT COUNT(*) FROM per_event"
    )
    assert all(v.passed for v in t2s.validate_sql(sql))


# ---------------------------------------------------------------------------
# enforce_limit
# ---------------------------------------------------------------------------


def test_limit_injected_when_absent():
    assert f"LIMIT {t2s.SQL_ROW_LIMIT}" in t2s.enforce_limit(GOOD_SQL)


def test_oversized_limit_clamped():
    assert f"LIMIT {t2s.SQL_ROW_LIMIT}" in t2s.enforce_limit(GOOD_SQL + " LIMIT 999999")


def test_smaller_limit_preserved():
    assert "LIMIT 5" in t2s.enforce_limit(GOOD_SQL + " LIMIT 5")


# ---------------------------------------------------------------------------
# Text2SqlService.ask — end-to-end flows with fakes
# ---------------------------------------------------------------------------


def test_ok_flow_returns_rows_sql_and_answer():
    service = make_service()
    result = service.ask("How many events yesterday?")
    assert result["status"] == "ok"
    assert result["rows"] == [{"event_id": "abc"}]
    assert result["answer"] == "Found 1 event."
    assert result["sql"].startswith("SELECT")
    assert {v["name"] for v in result["guardrails"]} >= {"select_only", "dry_run", "row_limit"}


def test_refusal_flow():
    service = make_service(llm=FakeLlm("REFUSE: I only answer questions about live music."))
    result = service.ask("Write me a fibonacci function")
    assert result["status"] == "refused"
    assert "live music" in result["answer"]
    assert result["sql"] is None


def test_blocked_flow_never_executes():
    runner = FakeRunner()
    service = make_service(llm=FakeLlm("DROP TABLE dim_event"), runner=runner)
    result = service.ask("Please drop the events table")
    assert result["status"] == "blocked"
    assert runner.executed == []


def test_bytes_cap_blocks_before_execution():
    runner = FakeRunner(dry_bytes=t2s.DRY_RUN_MAX_BYTES + 1)
    service = make_service(runner=runner)
    result = service.ask("Scan everything")
    assert result["status"] == "blocked"
    assert runner.executed == []
    assert any(v["name"] == "bytes_estimate" and not v["passed"] for v in result["guardrails"])


def test_self_repair_retries_once_and_succeeds():
    llm = FakeLlm("SELECT bad_sql FROM fact_event_demand", GOOD_SQL, answer="Fixed it.")
    runner = FakeRunner(fail_dry_times=1)
    service = make_service(llm=llm, runner=runner)
    result = service.ask("Cheapest ticket?")
    assert result["status"] == "ok"
    assert "Unrecognized name" in llm.prompts[1]  # repair prompt carried the BQ error


def test_self_repair_gives_up_after_one_retry():
    runner = FakeRunner(fail_dry_times=99)
    service = make_service(llm=FakeLlm(GOOD_SQL), runner=runner)
    result = service.ask("Cheapest ticket?")
    assert result["status"] == "error"
    assert runner.executed == []


def test_answer_llm_failure_falls_back_to_template():
    class FlakyLlm(FakeLlm):
        def generate(self, prompt: str) -> str:
            if "Summarize" in prompt:
                raise RuntimeError("llm down")
            return GOOD_SQL

    service = make_service(llm=FlakyLlm(GOOD_SQL))
    result = service.ask("How many events?")
    assert result["status"] == "ok"
    assert "1 row" in result["answer"]


def test_markdown_fences_stripped():
    service = make_service(llm=FakeLlm(f"```sql\n{GOOD_SQL}\n```"))
    assert service.ask("q?")["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /ask endpoint contract
# ---------------------------------------------------------------------------


def test_ask_endpoint_contract():
    t2s.set_service(make_service())
    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "How many events yesterday?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body) >= {
        "status", "question", "sql", "rows", "row_count", "truncated",
        "answer", "guardrails", "bytes_processed", "model", "latency_ms",
    }


def test_ask_endpoint_rejects_short_question():
    t2s.set_service(make_service())
    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "hi"})
    assert response.status_code == 422  # pydantic min_length


def test_rate_limit_returns_first_class_status(monkeypatch):
    t2s.set_service(make_service())
    monkeypatch.setattr(t2s, "rate_limiter", t2s.RateLimiter(per_minute=2))
    monkeypatch.setattr("api.app.rate_limiter", t2s.rate_limiter)
    with TestClient(app) as client:
        for _ in range(2):
            assert client.post("/ask", json={"question": "How many?"}).json()["status"] == "ok"
        limited = client.post("/ask", json={"question": "How many?"}).json()
    assert limited["status"] == "rate_limited"


def test_row_truncation():
    rows = [{"n": i} for i in range(t2s.MAX_RESPONSE_ROWS + 10)]
    service = make_service(runner=FakeRunner(rows=rows))
    result = service.ask("List everything")
    assert result["row_count"] == t2s.MAX_RESPONSE_ROWS + 10
    assert len(result["rows"]) == t2s.MAX_RESPONSE_ROWS
    assert result["truncated"] is True


# ---------------------------------------------------------------------------
# Synth-mode toggle
# ---------------------------------------------------------------------------


def test_synth_service_blocks_real_tables():
    service = t2s.Text2SqlService(
        llm=FakeLlm(GOOD_SQL),  # tries to query fact_event_demand
        runner=FakeRunner(),
        schema_context="synth context",
        allowed_tables=t2s.ALLOWED_TABLES_SYNTH,
        dataset_label="synth",
    )
    result = service.ask("How many events?")
    assert result["status"] == "blocked"
    assert result["synthetic"] is True and result["dataset"] == "synth"


def test_synth_service_allows_synth_tables_and_labels_response():
    synth_sql = ("SELECT event_name FROM `data-architecture-498123.event_demand_synth"
                 ".synth_event_demand` WHERE sold_out")
    service = t2s.Text2SqlService(
        llm=FakeLlm(synth_sql),
        runner=FakeRunner(),
        schema_context="synth context",
        allowed_tables=t2s.ALLOWED_TABLES_SYNTH,
        dataset_label="synth",
    )
    result = service.ask("Which shows sold out?")
    assert result["status"] == "ok"
    assert result["synthetic"] is True


def test_ask_endpoint_routes_dataset_field():
    t2s.set_service(make_service())  # real mode
    synth = t2s.Text2SqlService(
        llm=FakeLlm("SELECT 1 FROM `p.d.synth_event_demand`"),
        runner=FakeRunner(),
        schema_context="synth",
        allowed_tables=t2s.ALLOWED_TABLES_SYNTH,
        dataset_label="synth",
    )
    t2s.set_service(synth, mode="synth")
    with TestClient(app) as client:
        real = client.post("/ask", json={"question": "How many events?"}).json()
        via_synth = client.post(
            "/ask", json={"question": "Which sold out?", "dataset": "synth"}
        ).json()
        bad = client.post("/ask", json={"question": "hi there", "dataset": "nope"})
    assert real["dataset"] == "real" and real["synthetic"] is False
    assert via_synth["dataset"] == "synth" and via_synth["synthetic"] is True
    assert bad.status_code == 422
    t2s.set_service(None, mode="synth")
