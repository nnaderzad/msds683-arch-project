"""Text-to-SQL agent over the curated star schema (lakehouse deep dive).

Pipeline: question -> Gemini on Vertex AI (reads the committed
``api/schema_context.md``) -> **layered guardrails** -> BigQuery -> short
natural-language answer. Every layer emits a verdict the UI can badge:

  1. Prompt layer — the model must answer with ``REFUSE: <reason>`` for
     off-domain questions or ones needing forbidden semantics (e.g. averaging
     Trends interest across artists).
  2. AST validation (sqlglot) — exactly one SELECT statement, no DML/DDL nodes,
     every referenced table on the allow-list below.
  3. BigQuery dry-run — schema/syntax gate + bytes estimate (blocks > 512 MiB);
     one self-repair retry feeds the dry-run error back to the model.
  4. Execution caps — ``maximum_bytes_billed`` 1 GiB, ``LIMIT`` injection,
     20 s timeout, response truncated to 50 rows. A guardrail-clean query that
     matches ZERO rows while filtering on a string literal earns one corrective
     regeneration (``zero_row_retry``) — guessed categorical literals (genre
     spellings, metro names) are the dominant real-user failure mode; the retry
     passes the exact same gates and never loosens a guardrail.

Follow-up questions: ``ask()`` accepts up to 3 prior ``{question, answer}``
turns and renders them into the SQL prompt so "those shows" resolves correctly.
  5. IAM backstop — the service account is BigQuery jobUser + dataset
     dataViewer only: even a validator bypass physically cannot write.
  6. Abuse caps — per-client 6 questions/min + a global daily counter
     (in-process; resets on cold start — acceptable for the demo window).

The LLM and BigQuery clients are injectable (``set_service``), mirroring
``api/gold.py``'s repository seam, so the offline test suite exercises the whole
pipeline with fakes and zero credentials.

Why the allow-list excludes what it excludes (the deep-dive's "schema
enabled/constrained the agent" story, see ``docs/data-model.md``):

  * ``fact_event_demand_continuous`` — team-derived, forward-filled demo table;
    letting the agent read it would poison coverage/count answers.
  * ``tm_events`` — current-state MERGE that carries the last price forward;
    price *history* must come from observed-only tables.
  * ``tm_observations`` — superseded by ``fact_ticketmaster`` (same grain plus
    capture provenance).
  * backup/staging tables (``*_bak_*``, ``*_staging``) are never exposed.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import sqlglot
from sqlglot import expressions as exp

from api.gold import DEFAULT_PROJECT, _clean

# Gold + conformed dims + the observed-only silver facts. Grains and join keys are
# documented in docs/data-model.md and rendered for the LLM by
# eda/build_schema_context.py (single source of truth for prompt + validator).
ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        # gold
        "fact_event_demand",
        "forecast_event_price",
        # conformed dimensions
        "dim_event",
        "dim_artist",
        "dim_venue",
        "dim_geo",
        "dim_date",
        "bridge_event_artist",
        # silver facts (observed-only)
        "fact_ticketmaster",
        "fact_trends",
        "fact_trends_daily",
        "fact_youtube",
    }
)

# Synthetic sandbox mode: the agent may query ONLY the clearly-labeled synth
# tables in `event_demand_synth` (sellout/resale infill — data no real source
# provides). Kept disjoint from the honest allow-list so a synth answer can
# never silently mix with observed data.
ALLOWED_TABLES_SYNTH: frozenset[str] = frozenset(
    {"synth_event_demand", "synth_resale_series"}
)

SCHEMA_CONTEXT_PATH = Path(__file__).parent / "schema_context.md"
SCHEMA_CONTEXT_SYNTH_PATH = Path(__file__).parent / "schema_context_synth.md"

SQL_ROW_LIMIT = 200          # LIMIT injected/clamped into every query
MAX_RESPONSE_ROWS = 50       # rows returned to the client
DRY_RUN_MAX_BYTES = 512 * 1024**2
MAX_BYTES_BILLED = 1024**3
QUERY_TIMEOUT_S = 20
RATE_PER_MINUTE = 6
DAILY_QUESTION_CAP = 300

REFUSE_PREFIX = "REFUSE:"

MAX_HISTORY_TURNS = 3        # prior exchanges included in the prompt
MAX_HISTORY_FIELD_CHARS = 600

# One corrective regeneration when a guardrail-clean query matches nothing: the
# dominant cause is a guessed categorical literal (genre spelling, metro name)
# instead of the canonical vocabulary in the schema context.
ZERO_ROW_NOTE = (
    "The previous SQL compiled and executed but returned ZERO rows:\n{sql}\n"
    "The most common cause is a string literal that does not match how values are "
    "stored — re-check the canonical values and geography rules in the schema "
    "context (genres use Ticketmaster labels such as 'Dance/Electronic'; geography "
    "filters use dma_code, e.g. '807' for the San Francisco Bay Area). Return a "
    "corrected SELECT if a mismatch is plausible, or the same SQL if it is "
    "genuinely correct."
)

# DML/DDL/admin node types rejected outright by the AST gate. sqlglot parses
# EXECUTE IMMEDIATE / scripting statements as Command.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Grant,
    exp.TruncateTable,
    exp.Command,
)


@dataclass(frozen=True)
class GuardrailVerdict:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]
    total_bytes_processed: int | None


class LlmClient(Protocol):
    def generate(self, prompt: str) -> str: ...


class QueryRunner(Protocol):
    def dry_run(self, sql: str) -> int:
        """Validate without executing; return the bytes-processed estimate."""
        ...

    def run(self, sql: str) -> QueryResult: ...


# ---------------------------------------------------------------------------
# Guardrail layer 2: AST validation (pure, offline-testable)
# ---------------------------------------------------------------------------


def _referenced_tables(statement: exp.Expression) -> tuple[set[str], set[str]]:
    """Return (real table names, CTE alias names) referenced by the statement."""
    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    tables = {t.name for t in statement.find_all(exp.Table)}
    return tables - cte_names, cte_names


def validate_sql(sql: str, allowed: frozenset[str] = ALLOWED_TABLES) -> list[GuardrailVerdict]:
    """AST-gate the generated SQL: one SELECT, no writes, allow-listed tables only."""
    try:
        statements = sqlglot.parse(sql, dialect="bigquery")
    except sqlglot.errors.ParseError as err:
        return [GuardrailVerdict("select_only", False, f"SQL failed to parse: {err}")]

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return [
            GuardrailVerdict(
                "select_only", False, f"expected exactly one statement, got {len(statements)}"
            )
        ]
    statement = statements[0]

    verdicts: list[GuardrailVerdict] = []
    forbidden = [
        node.__class__.__name__
        for node in statement.walk()
        if isinstance(node, _FORBIDDEN_NODES)
    ]
    if not isinstance(statement, (exp.Select, exp.Union)) or forbidden:
        kinds = ", ".join(sorted(set(forbidden))) or statement.__class__.__name__
        verdicts.append(
            GuardrailVerdict("select_only", False, f"only SELECT is allowed (found: {kinds})")
        )
    else:
        verdicts.append(GuardrailVerdict("select_only", True, "single SELECT statement"))

    tables, _ctes = _referenced_tables(statement)
    outside = sorted(t for t in tables if t not in allowed)
    if outside:
        verdicts.append(
            GuardrailVerdict(
                "table_allowlist", False, f"tables outside the allow-list: {', '.join(outside)}"
            )
        )
    else:
        verdicts.append(
            GuardrailVerdict(
                "table_allowlist", True, f"{len(tables)} referenced table(s) all allow-listed"
            )
        )
    return verdicts


def _has_string_literal_filter(sql: str) -> bool:
    """True when the WHERE clause compares against a string literal (a retry candidate)."""
    try:
        statement = sqlglot.parse_one(sql, dialect="bigquery")
    except sqlglot.errors.ParseError:
        return False
    where = statement.find(exp.Where)
    if where is None:
        return False
    return any(
        isinstance(node, exp.Literal) and node.is_string for node in where.find_all(exp.Literal)
    )


def enforce_limit(sql: str, max_rows: int = SQL_ROW_LIMIT) -> str:
    """Inject ``LIMIT max_rows`` when absent; clamp an existing larger LIMIT."""
    statement = sqlglot.parse_one(sql, dialect="bigquery")
    limit = statement.args.get("limit")
    if limit is None:
        statement = statement.limit(max_rows)
    else:
        try:
            current = int(limit.expression.this)
        except (TypeError, ValueError):
            current = None
        if current is None or current > max_rows:
            statement.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return statement.sql(dialect="bigquery")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def _render_history(history: list[dict[str, str]] | None) -> str:
    """Render prior Q&A turns for follow-up questions ("those shows", "that artist")."""
    if not history:
        return ""
    lines = [
        "\nPrevious exchanges in this conversation — use them to resolve follow-up "
        "references; the new question below may build on them:"
    ]
    for turn in history[-MAX_HISTORY_TURNS:]:
        question = str(turn.get("question", ""))[:MAX_HISTORY_FIELD_CHARS]
        answer = str(turn.get("answer", ""))[:MAX_HISTORY_FIELD_CHARS]
        if question:
            lines.append(f"Q: {question}")
        if answer:
            lines.append(f"A: {answer}")
    return "\n".join(lines) + "\n"


def build_sql_prompt(
    schema_context: str,
    question: str,
    previous_error: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> str:
    repair = ""
    if previous_error:
        repair = (
            "\nYour previous SQL failed BigQuery validation with this error — return a "
            f"corrected query (or refuse):\n{previous_error}\n"
        )
    return (
        "You are the SQL analyst for the live-music demand warehouse described below.\n"
        "Answer the user's question with BigQuery Standard SQL.\n\n"
        "Rules:\n"
        "- Output EXACTLY one SELECT statement, fully qualified table names, no markdown "
        "fences, no commentary.\n"
        "- Use only the tables in the schema context and obey its semantic rules.\n"
        f"- If the question is off-domain (not about this warehouse's events, artists, venues, "
        f"prices, interest, or forecasts), requires a comparison the semantic rules forbid, or "
        f"asks for anything other than reading data, output exactly: {REFUSE_PREFIX} <one "
        "short, polite sentence>.\n\n"
        f"{schema_context}\n{_render_history(history)}{repair}\nQuestion: {question}\nSQL:"
    )


def build_answer_prompt(question: str, sql: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "Summarize an empty query result in 1-2 plain sentences for a non-technical "
            "music fan: say that no records matched THIS query, naming the key filter "
            "values it used. Never claim the warehouse tracks nothing — only that this "
            "specific query matched nothing.\n"
            f"Question: {question}\nSQL used: {sql}\nAnswer:"
        )
    sample = json.dumps(rows[:20], default=str)
    if len(sample) > 4000:
        sample = sample[:4000] + "…"
    return (
        "Summarize this query result in 1-3 plain sentences for a non-technical music fan.\n"
        "State only what the rows show — no speculation, no advice. Include concrete numbers.\n"
        f"Question: {question}\nSQL used: {sql}\nRows ({len(rows)} total): {sample}\nAnswer:"
    )


# ---------------------------------------------------------------------------
# Live clients (lazy imports so offline tests never need SDKs or credentials)
# ---------------------------------------------------------------------------


class VertexLlmClient:
    """Gemini on Vertex AI via the google-genai SDK (ADC / service-account auth)."""

    def __init__(self, model: str | None = None, location: str | None = None):
        self.model = model or os.environ.get("TEXT2SQL_MODEL", "gemini-2.5-flash")
        self.location = location or os.environ.get("VERTEX_LOCATION", "global")
        self._client = None

    def generate(self, prompt: str) -> str:
        from google import genai
        from google.genai import types

        if self._client is None:
            project = os.environ.get("DBT_GCP_PROJECT", DEFAULT_PROJECT)
            self._client = genai.Client(vertexai=True, project=project, location=self.location)
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                seed=683,
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return (response.text or "").strip()


class BigQueryRunner:
    """Read-only BigQuery execution with dry-run gate and billing caps."""

    def __init__(self, project: str | None = None):
        self.project = project or os.environ.get("DBT_GCP_PROJECT", DEFAULT_PROJECT)
        self._client = None

    def _bq(self):
        from google.cloud import bigquery

        if self._client is None:
            self._client = bigquery.Client(project=self.project)
        return self._client, bigquery

    def dry_run(self, sql: str) -> int:
        client, bigquery = self._bq()
        job = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True))
        return int(job.total_bytes_processed or 0)

    def run(self, sql: str) -> QueryResult:
        client, bigquery = self._bq()
        job = client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=MAX_BYTES_BILLED,
                labels={"app": "text2sql"},
            ),
        )
        rows = [
            {key: _clean(value) for key, value in dict(row).items()}
            for row in job.result(timeout=QUERY_TIMEOUT_S)
        ]
        return QueryResult(rows=rows, total_bytes_processed=job.total_bytes_processed)


# ---------------------------------------------------------------------------
# Guardrail layer 6: in-process rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Per-client sliding-minute cap + global daily counter (UTC reset)."""

    def __init__(self, per_minute: int = RATE_PER_MINUTE, daily_cap: int = DAILY_QUESTION_CAP):
        self.per_minute = per_minute
        self.daily_cap = daily_cap
        self._recent: dict[str, deque[float]] = defaultdict(deque)
        self._day = ""
        self._day_count = 0

    def check(self, client_key: str, now: float | None = None) -> str | None:
        """Record one question; return a refusal detail string when over a cap."""
        now = time.monotonic() if now is None else now
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self._day:
            self._day, self._day_count = today, 0
        if self._day_count >= self.daily_cap:
            return f"daily question budget ({self.daily_cap}) exhausted — try again tomorrow"
        window = self._recent[client_key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return f"rate limit: max {self.per_minute} questions/minute — wait a moment"
        window.append(now)
        self._day_count += 1
        return None


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class Text2SqlService:
    def __init__(
        self,
        llm: LlmClient,
        runner: QueryRunner,
        schema_context: str,
        model: str = "",
        allowed_tables: frozenset[str] = ALLOWED_TABLES,
        dataset_label: str = "real",
    ):
        self.llm = llm
        self.runner = runner
        self.schema_context = schema_context
        self.model = model
        self.allowed_tables = allowed_tables
        self.dataset_label = dataset_label

    def _respond(self, status: str, question: str, started: float, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "question": question,
            "sql": None,
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "answer": None,
            "guardrails": [],
            "bytes_processed": None,
            "model": self.model,
            "dataset": self.dataset_label,
            "synthetic": self.dataset_label == "synth",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        payload.update(extra)
        return payload

    def ask(self, question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        started = time.monotonic()

        sql, refusal, error = self._generate_sql(question, history=history)
        if refusal is not None:
            verdict = GuardrailVerdict("llm_domain_check", True, "model refused out-of-scope ask")
            return self._respond(
                "refused", question, started, answer=refusal, guardrails=[verdict.as_dict()]
            )
        if sql is None:
            return self._respond("error", question, started, answer=error)

        verdicts = validate_sql(sql, self.allowed_tables)
        if not all(v.passed for v in verdicts):
            return self._respond(
                "blocked",
                question,
                started,
                sql=sql,
                answer="That request was blocked by the SQL guardrails.",
                guardrails=[v.as_dict() for v in verdicts],
            )

        sql = enforce_limit(sql)
        verdicts.append(GuardrailVerdict("row_limit", True, f"LIMIT {SQL_ROW_LIMIT} enforced"))

        sql, dry_bytes, dry_verdict = self._dry_run_with_repair(question, sql, history)
        verdicts.append(dry_verdict)
        if not dry_verdict.passed:
            status = "blocked" if "estimate" in dry_verdict.name else "error"
            return self._respond(
                status,
                question,
                started,
                sql=sql,
                answer="That query was rejected before execution.",
                guardrails=[v.as_dict() for v in verdicts],
            )

        try:
            result = self.runner.run(sql)
        except Exception as err:  # noqa: BLE001 - surface any execution failure as status=error
            return self._respond(
                "error",
                question,
                started,
                sql=sql,
                answer=f"Query execution failed: {err}",
                guardrails=[v.as_dict() for v in verdicts],
            )

        if not result.rows and _has_string_literal_filter(sql):
            sql, result, retry_verdict = self._zero_row_retry(question, sql, result, history)
            verdicts.append(retry_verdict)

        rows = result.rows
        try:
            answer = self.llm.generate(build_answer_prompt(question, sql, rows))
        except Exception:  # noqa: BLE001 - answer text is optional; fall back to a template
            answer = f"Query returned {len(rows)} row(s)."
        return self._respond(
            "ok",
            question,
            started,
            sql=sql,
            rows=rows[:MAX_RESPONSE_ROWS],
            row_count=len(rows),
            truncated=len(rows) > MAX_RESPONSE_ROWS,
            answer=answer,
            guardrails=[v.as_dict() for v in verdicts],
            bytes_processed=result.total_bytes_processed,
        )

    def _zero_row_retry(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        history: list[dict[str, str]] | None,
    ) -> tuple[str, QueryResult, GuardrailVerdict]:
        """One corrective regeneration when a guardrail-clean query matches nothing.

        The retry must survive the exact same gates (AST, LIMIT, dry-run cap) as the
        original; on any failure — or a still-empty result — the original empty
        result is kept, so this can widen answers but never loosen a guardrail.
        """
        repaired, _refusal, _gen_error = self._generate_sql(
            question, ZERO_ROW_NOTE.format(sql=sql), history
        )
        if repaired is None:
            return sql, result, GuardrailVerdict(
                "zero_row_retry", True, "0 rows; model kept its query — result confirmed"
            )
        if not all(v.passed for v in validate_sql(repaired, self.allowed_tables)):
            return sql, result, GuardrailVerdict(
                "zero_row_retry", True, "0 rows; corrected query failed guardrails — kept original"
            )
        repaired = enforce_limit(repaired)
        # Both sides are sqlglot-rendered, so string equality detects a no-op retry.
        if repaired.strip() == sql.strip():
            return sql, result, GuardrailVerdict(
                "zero_row_retry", True, "0 rows; model kept its query — result confirmed"
            )
        try:
            if self.runner.dry_run(repaired) > DRY_RUN_MAX_BYTES:
                return sql, result, GuardrailVerdict(
                    "zero_row_retry", True, "0 rows; corrected query over scan cap — kept original"
                )
            retried = self.runner.run(repaired)
        except Exception:  # noqa: BLE001 - a failed retry must never lose the original result
            return sql, result, GuardrailVerdict(
                "zero_row_retry", True, "0 rows; corrected query failed to run — kept original"
            )
        if retried.rows:
            return repaired, retried, GuardrailVerdict(
                "zero_row_retry",
                True,
                f"first query matched 0 rows; corrected literals matched {len(retried.rows)} row(s)",
            )
        return sql, result, GuardrailVerdict(
            "zero_row_retry", True, "0 rows; corrected query also matched nothing"
        )

    def _generate_sql(
        self,
        question: str,
        previous_error: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        """Return (sql, refusal_reason, error) — exactly one is non-None."""
        try:
            raw = self.llm.generate(
                build_sql_prompt(self.schema_context, question, previous_error, history)
            )
        except Exception as err:  # noqa: BLE001 - surface LLM outages as status=error
            return None, None, f"The language model is unavailable: {err}"
        text = raw.strip().removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
        if text.upper().startswith(REFUSE_PREFIX):
            return None, text[len(REFUSE_PREFIX):].strip() or "Out of scope.", None
        if not text:
            return None, None, "The model returned an empty response."
        return text, None, None

    def _dry_run_with_repair(
        self, question: str, sql: str, history: list[dict[str, str]] | None = None
    ) -> tuple[str, int | None, GuardrailVerdict]:
        """Dry-run the SQL; on failure, one self-repair regeneration attempt."""
        for attempt in (0, 1):
            try:
                estimate = self.runner.dry_run(sql)
            except Exception as err:  # noqa: BLE001 - feed BQ's error back to the model once
                if attempt == 1:
                    return sql, None, GuardrailVerdict("dry_run", False, str(err)[:300])
                repaired, refusal, gen_error = self._generate_sql(question, str(err), history)
                if repaired is None:
                    detail = refusal or gen_error or "self-repair failed"
                    return sql, None, GuardrailVerdict("dry_run", False, detail[:300])
                repaired_verdicts = validate_sql(repaired, self.allowed_tables)
                if not all(v.passed for v in repaired_verdicts):
                    return sql, None, GuardrailVerdict(
                        "dry_run", False, "self-repaired SQL failed guardrails"
                    )
                sql = enforce_limit(repaired)
                continue
            if estimate > DRY_RUN_MAX_BYTES:
                return sql, estimate, GuardrailVerdict(
                    "bytes_estimate",
                    False,
                    f"estimated scan {estimate / 1024**2:.0f} MiB exceeds the "
                    f"{DRY_RUN_MAX_BYTES / 1024**2:.0f} MiB cap",
                )
            return sql, estimate, GuardrailVerdict(
                "dry_run", True, f"compiled; estimated scan {estimate / 1024**2:.1f} MiB"
            )
        raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Process-wide singletons (same seam as api.gold.set_repository)
# ---------------------------------------------------------------------------

rate_limiter = RateLimiter()

_services: dict[str, Text2SqlService | None] = {"real": None, "synth": None}


def set_service(service: Text2SqlService | None, mode: str = "real") -> None:
    """Swap a process-wide service; tests pass fakes, None resets lazy init."""
    _services[mode] = service


def get_service(mode: str = "real") -> Text2SqlService:
    if mode not in _services:
        raise ValueError(f"unknown text2sql mode: {mode!r}")
    if _services[mode] is None:
        llm = VertexLlmClient()
        if mode == "synth":
            _services[mode] = Text2SqlService(
                llm=llm,
                runner=BigQueryRunner(),
                schema_context=SCHEMA_CONTEXT_SYNTH_PATH.read_text(encoding="utf-8"),
                model=llm.model,
                allowed_tables=ALLOWED_TABLES_SYNTH,
                dataset_label="synth",
            )
        else:
            _services[mode] = Text2SqlService(
                llm=llm,
                runner=BigQueryRunner(),
                schema_context=SCHEMA_CONTEXT_PATH.read_text(encoding="utf-8"),
                model=llm.model,
            )
    return _services[mode]
