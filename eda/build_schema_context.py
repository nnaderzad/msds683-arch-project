#!/usr/bin/env python3
"""Generate the committed LLM schema context for the text-to-SQL agent.

Produces ``api/schema_context.md`` — the complete "what the agent knows" bundle:
per-table columns pulled live from ``INFORMATION_SCHEMA`` (so it can never drift
from the warehouse), merged with curated grain/semantic notes transcribed from
``docs/data-model.md``, a join map, the semantic guardrail rules, and few-shot
question->SQL examples that are **dry-run validated** at generation time (the
generator fails if an example stops compiling against the live schema).

Precedent: ``eda/hero_candidates.py`` generating ``web/src/data/heroShows.ts`` —
a deterministic generator emitting a committed consumable. Re-run after any
schema change and commit the diff:

    python eda/build_schema_context.py

Deterministic given the warehouse state; the only LLM involvement is *reading*
the output at serve time. A hard character budget keeps the prompt bounded.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
sys.path.insert(0, str(REPO_ROOT))
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, bq_rows, utc_now_iso  # noqa: E402

from api.text2sql import ALLOWED_TABLES  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "api" / "schema_context.md"
# Hard prompt budget (~4k tokens at ~4 chars/token). Generation FAILS above this so
# the context can never silently balloon past what a cheap Flash call handles well.
MAX_CHARS = 16_000

# ---------------------------------------------------------------------------
# Curated semantics (transcribed from docs/data-model.md — keep the two in sync).
# ---------------------------------------------------------------------------

TABLE_NOTES: dict[str, str] = {
    "fact_event_demand": (
        "GOLD star. Grain: one row per (event_id, snapshot_date) — one capture day of one "
        "event. Observed-only: price columns are NULL on days Ticketmaster showed no price "
        "(never forward-filled). Join dims via artist_id/venue_id/dma_code/show dates."
    ),
    "forecast_event_price": (
        "GOLD forecast (anchor+drift model). Grain: (event_id, days_to_show) from run day "
        "to show day. The predicted price nearest the show is the row with MIN(days_to_show)."
    ),
    "dim_event": "One row per event. show_date is the CONCERT day (not a capture day).",
    "dim_artist": (
        "One row per artist. artist_id is a deterministic hash of the normalized name; "
        "match names case-insensitively on artist_name."
    ),
    "dim_venue": (
        "One row per venue. capacity is curated from public sources and may be NULL "
        "(never guess a missing capacity)."
    ),
    "dim_geo": "Nielsen DMA metro lookup. dma_code is the bare code (e.g. '807' = SF Bay Area).",
    "dim_date": "Calendar helper (weekend/holiday/season flags). Join on any DATE column.",
    "bridge_event_artist": (
        "Event<->artist many-to-many with is_headliner + billing_order. Gold facts keep the "
        "HEADLINER only; use this bridge for full lineups."
    ),
    "fact_ticketmaster": (
        "SILVER price-history spine. Grain: (event_id, snapshot_date), observed-only, with "
        "capture provenance (n_captures, price_disagreed)."
    ),
    "fact_trends": (
        "SILVER Google Trends CROSS-METRO snapshot. Grain: (artist_id, dma_code, "
        "snapshot_date). interest compares METROS for one artist at one moment."
    ),
    "fact_trends_daily": (
        "SILVER Google Trends DAILY trajectory. Grain: (artist_id, dma_code, snapshot_date "
        "= the interest day). interest compares DAYS for one (artist, metro)."
    ),
    "fact_youtube": "SILVER daily YouTube channel stats per artist (subscribers, views).",
}

COLUMN_NOTES: dict[tuple[str, str], str] = {
    ("fact_event_demand", "snapshot_date"): "capture day (NOT the concert day)",
    ("fact_event_demand", "days_to_show"): "show_date - snapshot_date in days",
    ("fact_event_demand", "price_min"): "observed min ticket price that day; NULL = not shown",
    ("fact_event_demand", "local_interest"): (
        "Google Trends 0-100 for the artist in the venue's metro; comparable over time "
        "within one (artist, metro) only"
    ),
    ("fact_event_demand", "dma_code"): "venue's Nielsen DMA (join dim_geo)",
    ("forecast_event_price", "days_to_show"): "days before the show this prediction targets",
    ("dim_event", "show_date"): "concert day",
    ("dim_venue", "capacity"): "researched real capacity; NULL where unknown",
    ("fact_trends", "interest"): (
        "0-100 normalized PER PULL: cross-metro ranking for one artist; NEVER compare or "
        "average across artists"
    ),
    ("fact_trends_daily", "interest"): (
        "0-100 normalized PER PULL: daily trajectory for one (artist, metro); NEVER compare "
        "or average across artists or metros"
    ),
    ("fact_ticketmaster", "n_captures"): "intra-day captures collapsed into this row",
    ("fact_ticketmaster", "price_disagreed"): "captures that day disagreed on price",
    ("fact_youtube", "official_subscribers"): "channel subscribers on snapshot_date",
}

JOIN_MAP = """\
- fact_event_demand.event_id = dim_event.event_id ; dim_event.venue_id = dim_venue.venue_id
- fact_event_demand.artist_id = dim_artist.artist_id (headliner only)
- fact_event_demand.dma_code = dim_geo.dma_code (bare code, e.g. '807' — never 'US-CA-807')
- fact_event_demand.snapshot_date = dim_date.date ; dim_event.show_date = dim_date.date
- bridge_event_artist: event_id <-> artist_id (is_headliner, billing_order) for full lineups
- forecast_event_price.event_id = dim_event.event_id (align on days_to_show vs today)
- fact_trends*/fact_youtube join facts on artist_id (+ dma_code, snapshot_date)"""

SEMANTIC_RULES = """\
1. Google Trends `interest` is normalized 0-100 WITHIN each pull. NEVER compare, rank, or
   average it across artists, and never across metros in fact_trends_daily. If a question
   requires that comparison, refuse and explain the normalization.
2. Two date meanings: dim_event.show_date = the concert day; snapshot_date on every fact =
   the capture day; days_to_show is the gap. "Events in August" means show_date in August.
3. Price history is OBSERVED-ONLY: NULL price means "not listed that day", not zero and not
   missing-at-random (~23% of events ever show a Ticketmaster price). Count coverage with
   COUNTIF(price_min IS NOT NULL); never treat NULL days as sellouts or price drops.
4. Gold keeps the HEADLINER only; support-act questions need bridge_event_artist.
5. Only SELECT statements over the tables listed here. Refuse anything else politely —
   including questions unrelated to the event-demand domain."""

# Few-shot examples — each is dry-run compiled against the live dataset at generation
# time, so a schema change that breaks an example fails the build, not the demo.
FEW_SHOTS: list[tuple[str, str]] = [
    (
        "What is the cheapest ticket price ever observed for Everclear?",
        """SELECT MIN(f.price_min) AS cheapest_price
FROM {ds}.fact_event_demand f
JOIN {ds}.dim_artist a ON f.artist_id = a.artist_id
WHERE LOWER(a.artist_name) = 'everclear' AND f.price_min IS NOT NULL""",
    ),
    (
        "When is the next show at The Independent in San Francisco?",
        """SELECT MIN(e.show_date) AS next_show
FROM {ds}.dim_event e
JOIN {ds}.dim_venue v ON e.venue_id = v.venue_id
WHERE LOWER(v.venue_name) = 'the independent' AND LOWER(v.city) = 'san francisco'
  AND e.show_date >= CURRENT_DATE()""",
    ),
    (
        "What price does the model predict at show time for event rZ7HnEZ1Af00jd?",
        """SELECT predicted_price
FROM {ds}.forecast_event_price
WHERE event_id = 'rZ7HnEZ1Af00jd'
QUALIFY ROW_NUMBER() OVER (ORDER BY days_to_show ASC) = 1""",
    ),
    (
        "For each genre, how many events do we track and what share ever showed a price?",
        """WITH per_event AS (
  SELECT e.event_id, e.primary_genre,
         COUNTIF(f.price_min IS NOT NULL) > 0 AS ever_priced
  FROM {ds}.dim_event e
  JOIN {ds}.fact_event_demand f ON f.event_id = e.event_id
  GROUP BY e.event_id, e.primary_genre
)
SELECT primary_genre, COUNT(*) AS events,
       ROUND(COUNTIF(ever_priced) / COUNT(*) * 100, 1) AS pct_ever_priced
FROM per_event GROUP BY primary_genre ORDER BY events DESC""",
    ),
    (
        "How has local interest in Everclear moved in the SF metro over the last 60 days?",
        """SELECT t.snapshot_date, t.interest
FROM {ds}.fact_trends_daily t
JOIN {ds}.dim_artist a ON t.artist_id = a.artist_id
WHERE LOWER(a.artist_name) = 'everclear' AND t.dma_code = '807'
  AND t.snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
ORDER BY t.snapshot_date""",
    ),
]

FACT_DATE_COLUMN = "snapshot_date"


# ---------------------------------------------------------------------------
# Pure rendering (offline-tested in tests/test_build_schema_context.py)
# ---------------------------------------------------------------------------


def render_context(
    columns: list[dict[str, str]],
    stats: dict[str, dict[str, str]],
    project: str,
    dataset: str,
    as_of: str,
) -> str:
    """Render the full markdown context from schema rows + per-table stats."""

    tables: dict[str, list[dict[str, str]]] = {}
    for row in columns:
        tables.setdefault(row["table_name"], []).append(row)
    missing = ALLOWED_TABLES - tables.keys()
    if missing:
        raise SystemExit(f"allow-listed tables missing from INFORMATION_SCHEMA: {sorted(missing)}")
    unnoted = ALLOWED_TABLES - TABLE_NOTES.keys()
    if unnoted:
        raise SystemExit(f"allow-listed tables missing a TABLE_NOTES entry: {sorted(unnoted)}")

    lines: list[str] = [
        "<!-- Generated by eda/build_schema_context.py — do not edit by hand. -->",
        f"<!-- As of {as_of}. Re-run the generator after any schema change. -->",
        "",
        f"# Warehouse: `{project}.{dataset}` (BigQuery, event-demand star schema)",
        "",
        "Bay-Area-centric live-music demand warehouse: Ticketmaster prices, Google Trends",
        "interest, YouTube stats, conformed dims, and a price forecast. Dates are UTC days.",
        "",
        "## Semantic rules (non-negotiable)",
        "",
        SEMANTIC_RULES,
        "",
        "## Join map",
        "",
        JOIN_MAP,
        "",
        "## Tables",
    ]
    for name in sorted(tables, key=lambda t: (not t.startswith("fact_event"), t)):
        stat = stats.get(name, {})
        meta = f"{stat.get('row_count', '?')} rows"
        if stat.get("latest"):
            meta += f", latest {FACT_DATE_COLUMN} {stat['latest']}"
        lines += ["", f"### {name} ({meta})", "", TABLE_NOTES[name], ""]
        for col in tables[name]:
            note = COLUMN_NOTES.get((name, col["column_name"]))
            suffix = f" — {note}" if note else ""
            lines.append(f"- `{col['column_name']}` {col['data_type']}{suffix}")

    lines += ["", "## Examples", ""]
    for question, sql in FEW_SHOTS:
        rendered = sql.format(ds=f"`{project}.{dataset}`")
        lines += [f"Q: {question}", "```sql", rendered, "```", ""]

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        raise SystemExit(
            f"schema context is {len(text)} chars (> {MAX_CHARS}); trim COLUMN_NOTES/FEW_SHOTS"
        )
    return text


# ---------------------------------------------------------------------------
# Live I/O
# ---------------------------------------------------------------------------


def fetch_columns(project: str, dataset: str) -> list[dict[str, str]]:
    names = ", ".join(f"'{t}'" for t in sorted(ALLOWED_TABLES))
    sql = (
        f"SELECT table_name, column_name, data_type "
        f"FROM `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMNS "
        f"WHERE table_name IN ({names}) ORDER BY table_name, ordinal_position"
    )
    return bq_rows(sql, project)


def fetch_stats(project: str, dataset: str) -> dict[str, dict[str, str]]:
    names = ", ".join(f"'{t}'" for t in sorted(ALLOWED_TABLES))
    counts = bq_rows(
        f"SELECT table_id, row_count FROM `{project}.{dataset}`.__TABLES__ "
        f"WHERE table_id IN ({names})",
        project,
    )
    stats = {r["table_id"]: {"row_count": r["row_count"]} for r in counts}
    dated = [t for t in sorted(ALLOWED_TABLES) if t.startswith("fact_")]
    union = " UNION ALL ".join(
        f"SELECT '{t}' AS t, CAST(MAX({FACT_DATE_COLUMN}) AS STRING) AS latest "
        f"FROM `{project}.{dataset}.{t}`"
        for t in dated
    )
    for row in bq_rows(union, project):
        stats.setdefault(row["t"], {})["latest"] = row["latest"]
    return stats


def dry_run_examples(project: str, dataset: str) -> None:
    """Compile every few-shot against the live schema; fail generation on error."""

    for question, sql in FEW_SHOTS:
        rendered = sql.format(ds=f"`{project}.{dataset}`")
        proc = subprocess.run(
            ["bq", f"--project_id={project}", "query", "--use_legacy_sql=false", "--dry_run",
             rendered],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"few-shot failed dry-run: {question!r}\n{proc.stderr.strip()}")
        print(f"[build_schema_context] dry-run OK: {question}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument(
        "--skip-validation", action="store_true", help="skip the few-shot dry-run (offline)"
    )
    args = parser.parse_args()

    if not args.skip_validation:
        dry_run_examples(args.project, args.dataset)
    text = render_context(
        fetch_columns(args.project, args.dataset),
        fetch_stats(args.project, args.dataset),
        args.project,
        args.dataset,
        utc_now_iso(),
    )
    args.output.write_text(text + "\n", encoding="utf-8")
    print(f"[build_schema_context] wrote {args.output} ({len(text)} chars, ~{len(text)//4} tokens)")


if __name__ == "__main__":
    main()
