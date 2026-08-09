"""Durable thumbs-up/down feedback on text-to-SQL answers.

Rows stream into ``event_demand_ops.ask_feedback`` — a dataset deliberately
separate from the analytical warehouse. The service account holds dataEditor on
the ops dataset ONLY, so the read-only IAM backstop on
``event_demand_analytics`` (guardrail layer 5 in ``api/text2sql.py``) is
untouched: the agent still cannot write to any table it can query.

Feedback is offline-curation fuel — thumbs-down rows get mined into the eval
set and schema-context fixes. Nothing here trains or mutates the model online.

The sink is injectable (``set_sink``), mirroring ``api/gold.py``'s repository
seam, so offline tests capture rows without credentials.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Protocol

from api.gold import DEFAULT_PROJECT

DEFAULT_OPS_DATASET = "event_demand_ops"
FEEDBACK_TABLE = "ask_feedback"


def client_hash(client_key: str) -> str:
    """Pseudonymous spam-triage key — raw IPs are never stored."""
    return hashlib.sha256(client_key.encode("utf-8")).hexdigest()[:12]


class FeedbackSink(Protocol):
    def record(self, row: dict[str, Any]) -> None: ...


class BigQueryFeedbackSink:
    """Streaming insert into the ops dataset (lazy client, ADC auth)."""

    def __init__(self, project: str | None = None, dataset: str | None = None):
        self.project = project or os.environ.get("DBT_GCP_PROJECT", DEFAULT_PROJECT)
        self.dataset = dataset or os.environ.get("OPS_BQ_DATASET", DEFAULT_OPS_DATASET)
        self._client = None

    def record(self, row: dict[str, Any]) -> None:
        from google.cloud import bigquery

        if self._client is None:
            self._client = bigquery.Client(project=self.project)
        table_id = f"{self.project}.{self.dataset}.{FEEDBACK_TABLE}"
        errors = self._client.insert_rows_json(table_id, [row])
        if errors:
            raise RuntimeError(f"feedback insert failed: {errors}")


_sink: FeedbackSink | None = None


def set_sink(sink: FeedbackSink | None) -> None:
    """Swap the process-wide sink; tests pass fakes, None resets lazy init."""
    global _sink
    _sink = sink


def get_sink() -> FeedbackSink:
    global _sink
    if _sink is None:
        _sink = BigQueryFeedbackSink()
    return _sink
