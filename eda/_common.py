"""Shared helpers for the deterministic EDA scripts in ``eda/``.

Thin, dependency-light utilities reused by ``profile_schema.py`` and
``tm_price_eda.py``: BigQuery access via the already-authenticated ``bq`` CLI (no
extra Python client), committed-CSV reading, and provenance stamping.

No LLM at runtime; every caller stays committed + re-runnable, so a teammate can
re-run the identical code against more data later and diff the results.
"""

from __future__ import annotations

import csv
import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"


def bq_rows(sql: str, project: str) -> list[dict[str, str]]:
    """Run a BigQuery SQL statement via the bq CLI and return rows as dicts.

    Uses the authenticated ``bq`` CLI (CSV output) so the EDA needs no extra
    Python client dependency and runs wherever the project is authed (ADC).
    """

    proc = subprocess.run(
        ["bq", f"--project_id={project}", "query", "--use_legacy_sql=false",
         "--format=csv", "--max_rows=1000000", sql],
        capture_output=True, text=True, check=True,
    )
    return list(csv.DictReader(io.StringIO(proc.stdout)))


def fq(project: str, dataset: str, table: str) -> str:
    """Backtick-quoted fully-qualified table id: `project.dataset.table`."""

    return f"`{project}.{dataset}.{table}`"


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a committed CSV into a list of row dicts."""

    with Path(path).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def utc_now_iso() -> str:
    """UTC timestamp (seconds precision) for stamping output provenance."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
