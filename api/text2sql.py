"""Text-to-SQL agent over the curated star schema (lakehouse deep dive).

This module will hold the ``/ask`` service: question -> Gemini (Vertex AI) ->
guardrail-validated SELECT -> BigQuery -> natural-language answer. The service
lands in the next PR (AGENT-2 on the lakehouse plan); this file starts with the
**table allow-list**, which is the single source of truth shared by:

  * the schema-context generator (``eda/build_schema_context.py``) — the agent
    is only taught tables it is allowed to query;
  * the SQL validator (here) — generated SQL may only reference these tables.

Why these tables and not everything in the dataset (the deep-dive's
"schema enabled/constrained the agent" story, see ``docs/data-model.md``):

  * ``fact_event_demand_continuous`` is EXCLUDED — it is a team-derived,
    forward-filled demo table; letting the agent read it would poison
    coverage/count answers with synthetic fill.
  * ``tm_events`` is EXCLUDED — current-state MERGE that carries the last price
    forward; price *history* questions must come from observed-only tables.
  * ``tm_observations`` is EXCLUDED as superseded: ``fact_ticketmaster`` has the
    same observed-only grain plus capture provenance (``n_captures``,
    ``price_disagreed``).
  * backup/staging tables (``*_bak_*``, ``*_staging``) are never exposed.
"""

from __future__ import annotations

# Gold + conformed dims + the observed-only silver facts. Grains and join keys are
# documented in docs/data-model.md and rendered for the LLM by
# eda/build_schema_context.py.
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
