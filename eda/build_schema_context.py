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

from api.text2sql import ALLOWED_TABLES, ALLOWED_TABLES_SYNTH  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "api" / "schema_context.md"
SYNTH_OUTPUT_PATH = REPO_ROOT / "api" / "schema_context_synth.md"
SYNTH_DATASET = "event_demand_synth"
# Hard prompt budget (~4.5k tokens at ~4 chars/token). Generation FAILS above this so
# the context can never silently balloon past what a cheap Flash call handles well.
# Raised 16k -> 18k on 2026-08-09 for the lead-lag few-shot (a real user question the
# agent wrongly refused), then -> 20k on 2026-08-10 for the fact_nineteenhz section
# (club-show coverage); still a small fraction of Flash's window at ~$0.0006/question.
MAX_CHARS = 21_000

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
        "to show day. 'At show time' = the days_to_show = 0 row, which exists for every "
        "forecasted event — filter WHERE days_to_show = 0 (works in plain aggregates; "
        "avoid QUALIFY when using aggregate functions)."
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
    "dim_geo": (
        "Nielsen DMA metro lookup. dma_code is the bare code (e.g. '807' = SF Bay Area). "
        "Filter geography by dma_code, NOT metro_name — metro_name strings are exact "
        "Nielsen labels (e.g. '807' is 'San Francisco-Oakland-San Jose CA'); never guess "
        "or abbreviate them."
    ),
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
    "fact_nineteenhz": (
        "SILVER Bay Area club/warehouse listings scraped daily from 19hz.info — the shows "
        "Ticketmaster misses (~75% carry a face price). SELF-CONTAINED: no event_id, no "
        "joins to the star; rows repeat per capture day, so ALWAYS dedupe with QUALIFY "
        "ROW_NUMBER() OVER (PARTITION BY title, venue, event_date ORDER BY snapshot_date "
        "DESC) = 1. event_date is the show day; genres is free text (match with LIKE)."
    ),
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
    ("dim_event", "primary_genre"): (
        "Ticketmaster segment genre; NULL when unclassified — exclude NULLs when "
        "listing or counting genres"
    ),
    ("dim_venue", "state_code"): (
        "two-letter US state, e.g. 'CA' — the ONLY state column (there is no `state`)"
    ),
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
    ("fact_nineteenhz", "event_date"): "the show day (this table has no show_date column)",
    ("fact_nineteenhz", "price_min"): "face price from the listing; NULL = not stated",
    ("fact_nineteenhz", "genres"): "free-text genre tags — filter with LIKE '%house%' etc.",
    ("fact_nineteenhz", "is_free"): "TRUE for free/donation events",
    ("fact_ticketmaster", "price_disagreed"): "captures that day disagreed on price",
    ("fact_youtube", "official_subscribers"): "channel subscribers on snapshot_date",
}

JOIN_MAP = """\
- fact_event_demand.event_id = dim_event.event_id ; dim_event.venue_id = dim_venue.venue_id
- fact_event_demand.artist_id = dim_artist.artist_id (headliner only)
- dim_event has NO artist column: to list an artist's events join dim_artist ->
  bridge_event_artist -> dim_event (one row per event). Joining through
  fact_event_demand instead fans out to one row per CAPTURE DAY — never do that for
  event lists, and never join artist_id to event_id directly
- fact_event_demand.dma_code = dim_geo.dma_code (bare code, e.g. '807' — never 'US-CA-807')
- fact_event_demand.snapshot_date = dim_date.date ; dim_event.show_date = dim_date.date
- bridge_event_artist: event_id <-> artist_id (is_headliner, billing_order) for full lineups
- forecast_event_price.event_id = dim_event.event_id (align on days_to_show vs today)
- fact_trends*/fact_youtube join facts on artist_id (+ dma_code, snapshot_date)"""

SEMANTIC_RULES = """\
1. Google Trends `interest` is normalized 0-100 WITHIN each pull. NEVER compare, rank, or
   average it across artists, and never across metros in fact_trends_daily. If a question
   requires that comparison, refuse and explain the normalization. But WITHIN one
   (artist, metro) series, comparison OVER TIME is the intended use: questions about an
   artist's interest rising or falling, or interest changes preceding price changes, are
   ANSWERABLE (per-event lead-lag counts aggregate within-event comparisons, which is
   safe — see the lead-lag example). Refuse only true cross-artist/cross-metro ranking.
2. Two date meanings: dim_event.show_date = the concert day; snapshot_date on every fact =
   the capture day; days_to_show is the gap. "Events in August" means show_date in August.
3. Price history is OBSERVED-ONLY: NULL price means "not listed that day", not zero and not
   missing-at-random (~26% of events ever show a Ticketmaster price). Count coverage with
   COUNTIF(price_min IS NOT NULL); never treat NULL days as sellouts or price drops.
4. Gold keeps the HEADLINER only; support-act questions need bridge_event_artist.
5. fact_event_demand has MANY rows per event (one per capture day). "Share/percentage of
   EVENTS" questions must aggregate per event first (GROUP BY event_id, or
   COUNT(DISTINCT event_id)) — a ratio over raw fact rows measures event-days, not events.
6. BigQuery dialect: QUALIFY cannot share a SELECT with aggregate functions. To aggregate
   over per-event nearest-forecast rows, QUALIFY inside a subquery/CTE first, then
   aggregate over it in the outer query.
7. Only SELECT statements over the tables listed here. Refuse anything else politely —
   including questions unrelated to the event-demand domain.
8. NEVER invent a literal for a categorical column (genre, metro, status). Copy strings
   from the "Canonical values" section EXACTLY; translate user slang via the alias list.
   If nothing there plausibly matches, refuse and name a few values that DO exist.
9. Geography: "Bay Area" / "San Francisco area" / "SF" questions filter dma_code = '807'
   (that metro includes Oakland, San Jose, Berkeley...). Filter dim_venue.city =
   'San Francisco' only when the user clearly means the city proper. Never filter on
   metro_name. "Shows we track / are tracking" = rows in dim_event (upcoming ones have
   show_date >= CURRENT_DATE()).
10. When listing or naming specific shows/events, ALSO select the event_id column —
   the UI turns rows carrying event_id into clickable links to that show's dashboard
   page. Leave event_id out of pure aggregates (counts, averages, shares).
11. Listing defaults (product rules) — apply ONLY when the answer returns rows of
   individual shows; NEVER add joins to counts or other aggregates (aggregate on the
   fewest tables the filters need — extra joins silently drop events with missing
   links). Show listings include artist_name and venue_name where those joins are
   cheap, and ORDER BY show_date ASC (soonest upcoming first) unless the user asks
   otherwise. "Biggest / most popular / big-name" questions rank by WORLDWIDE
   popularity = the headliner's latest fact_youtube.official_subscribers (latest
   snapshot per artist) — never by Trends interest (rule 1).
12. Ranking or filtering by ANY metric ("cheapest", "most expensive", "highest
   interest") REQUIRES `<metric> IS NOT NULL` in the WHERE — NULL means not-listed
   (rule 3) and NULLs sort FIRST ascending, so an unfiltered "cheapest" list is all
   blanks. "Current price" of an event = its LATEST snapshot's price (QUALIFY
   ROW_NUMBER per event ORDER BY snapshot_date DESC), never an arbitrary fact row.
13. "Cheap / club / warehouse / underground / small-venue" Bay Area questions:
   PREFER fact_nineteenhz — Ticketmaster structurally misses most club shows, so
   the star is thin there. Its rows have NO event_id (they won't link in the UI);
   say the listings come from 19hz.info. Big-venue/mainstream questions stay on
   the star."""

# Deterministic slang -> canonical-genre translations (Ticketmaster segment labels).
# Curated by hand — extend when live questions surface a new alias.
GENRE_ALIASES = (
    '"EDM" / "electronic" / "house" / "techno" / "dance music" -> \'Dance/Electronic\' ; '
    '"hip hop" / "rap" -> \'Hip-Hop/Rap\' ; "indie" -> \'Alternative\' ; '
    '"country music" -> \'Country\' ; "classical music" / "orchestra" -> \'Classical\''
)

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
        # Aggregating over at-show forecasts: QUALIFY can't share a SELECT with an
        # aggregate — pick the nearest-to-show row per event in a CTE, then aggregate.
        "What is the average predicted price at show time for each genre?",
        """WITH at_show AS (
  SELECT event_id, predicted_price
  FROM {ds}.forecast_event_price
  QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY days_to_show ASC) = 1
)
SELECT e.primary_genre, ROUND(AVG(s.predicted_price), 2) AS avg_predicted_price
FROM at_show s
JOIN {ds}.dim_event e ON e.event_id = s.event_id
GROUP BY e.primary_genre
ORDER BY avg_predicted_price DESC""",
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
    (
        # Real user question the agent got wrong by guessing a metro_name string:
        # area questions filter dma_code (rule 9), never metro_name/city guesses.
        "What shows are you tracking in the San Francisco Bay Area for the rest of 2026?",
        """SELECT e.event_id, e.event_name, e.show_date, v.venue_name, v.city
FROM {ds}.dim_event e
JOIN {ds}.dim_venue v ON e.venue_id = v.venue_id
WHERE v.dma_code = '807'
  AND e.show_date BETWEEN CURRENT_DATE() AND '2026-12-31'
ORDER BY e.show_date""",
    ),
    (
        # Real user question the agent got wrong by inventing a genre label:
        # "EDM" translates via the alias list to the canonical 'Dance/Electronic'.
        "What are some EDM shows coming up in San Francisco?",
        """SELECT e.event_id, e.event_name, e.show_date, v.venue_name
FROM {ds}.dim_event e
JOIN {ds}.dim_venue v ON e.venue_id = v.venue_id
WHERE e.primary_genre = 'Dance/Electronic' AND v.dma_code = '807'
  AND e.show_date >= CURRENT_DATE()
ORDER BY e.show_date""",
    ),
    (
        # Popularity ranking (rule 11): "biggest" = the headliner's latest worldwide
        # YouTube subscribers — never cross-artist Trends interest (rule 1).
        "What are the biggest shows coming to the Bay Area?",
        """WITH latest_subs AS (
  SELECT artist_id, official_subscribers
  FROM {ds}.fact_youtube
  QUALIFY ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY snapshot_date DESC) = 1
)
SELECT e.event_id, e.event_name, a.artist_name, e.show_date, v.venue_name,
       s.official_subscribers
FROM {ds}.dim_event e
JOIN {ds}.dim_venue v ON e.venue_id = v.venue_id
JOIN {ds}.bridge_event_artist b ON b.event_id = e.event_id AND b.is_headliner
JOIN {ds}.dim_artist a ON a.artist_id = b.artist_id
JOIN latest_subs s ON s.artist_id = a.artist_id
WHERE v.dma_code = '807' AND e.show_date >= CURRENT_DATE()
ORDER BY s.official_subscribers DESC
LIMIT 15""",
    ),
    (
        # Column-name pin: dim_venue has state_code (there is NO `state` column).
        "Which state has the most upcoming shows?",
        """SELECT v.state_code, COUNT(*) AS upcoming_shows
FROM {ds}.dim_event e
JOIN {ds}.dim_venue v ON e.venue_id = v.venue_id
WHERE e.show_date >= CURRENT_DATE()
GROUP BY v.state_code
ORDER BY upcoming_shows DESC
LIMIT 1""",
    ),
    (
        # Club-show coverage lives in fact_nineteenhz (rule 13): self-contained,
        # dedupe to the latest capture, price ranking needs IS NOT NULL (rule 12).
        "What are some cheap club shows in the Bay Area in the next two weeks?",
        """SELECT title, venue, city, event_date, price_min
FROM {ds}.fact_nineteenhz
WHERE event_date BETWEEN CURRENT_DATE() AND DATE_ADD(CURRENT_DATE(), INTERVAL 14 DAY)
  AND price_min IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY title, venue, event_date
                           ORDER BY snapshot_date DESC) = 1
ORDER BY price_min
LIMIT 15""",
    ),
    (
        # Lead-lag WITHIN each event's own series (rule 1's allowed direction):
        # per-event window comparisons, then aggregate the verdicts — never the
        # raw interest values — across events.
        "Does rising search interest for an artist playing in San Francisco precede rising prices?",
        """WITH price_rises AS (
  SELECT event_id, MIN(snapshot_date) AS first_rise
  FROM (
    SELECT event_id, snapshot_date, price_min,
           LAG(price_min) OVER (PARTITION BY event_id ORDER BY snapshot_date) AS prev_price
    FROM {ds}.fact_event_demand
    WHERE dma_code = '807' AND price_min IS NOT NULL
  )
  WHERE prev_price IS NOT NULL AND price_min > prev_price
  GROUP BY event_id
),
interest_windows AS (
  SELECT f.event_id,
         AVG(IF(f.snapshot_date >= DATE_SUB(p.first_rise, INTERVAL 14 DAY)
                AND f.snapshot_date < p.first_rise, f.local_interest, NULL)) AS pre_rise,
         AVG(IF(f.snapshot_date < DATE_SUB(p.first_rise, INTERVAL 14 DAY),
                f.local_interest, NULL)) AS baseline
  FROM {ds}.fact_event_demand f
  JOIN price_rises p ON f.event_id = p.event_id
  GROUP BY f.event_id
)
SELECT COUNT(*) AS bay_area_events_with_a_price_rise,
       COUNTIF(pre_rise > baseline) AS interest_was_rising_first,
       ROUND(COUNTIF(pre_rise > baseline) / COUNT(*) * 100, 1) AS pct_interest_led
FROM interest_windows
WHERE pre_rise IS NOT NULL AND baseline IS NOT NULL""",
    ),
]

FACT_DATE_COLUMN = "snapshot_date"

# ---------------------------------------------------------------------------
# SYNTH profile — the clearly-labeled synthetic sandbox (event_demand_synth).
# ---------------------------------------------------------------------------

SYNTH_TABLE_NOTES: dict[str, str] = {
    "synth_event_demand": (
        "SYNTHETIC sandbox. One row per REAL event, with SIMULATED sellout and resale "
        "outcomes generated by seeded heuristics (synth/heuristics.py) — no real source "
        "provides these. Carries denormalized event_name/venue_name/dma_code/primary_genre, "
        "researched venue capacity (capacity_source tells real vs tier_estimate), and "
        "provenance (synth_run_id, generator_version, seed)."
    ),
    "synth_resale_series": (
        "SYNTHETIC resale price trajectory per event over days_to_show checkpoints "
        "(face value at onsale converging to the final resale multiplier at the show)."
    ),
}

SYNTH_COLUMN_NOTES: dict[tuple[str, str], str] = {
    ("synth_event_demand", "demand_ratio"): "simulated demand ÷ capacity; >1 = oversubscribed",
    ("synth_event_demand", "sold_out"): "SIMULATED sellout verdict",
    ("synth_event_demand", "sellout_date"): "simulated sellout day (NULL if never sold out)",
    ("synth_event_demand", "capacity_source"): "'researched' (real, sourced) or 'tier_estimate'",
    ("synth_event_demand", "face_price_source"): "'observed' (real TM price) or fallback",
    ("synth_event_demand", "resale_multiplier"): "simulated resale price ÷ face at show time",
    ("synth_resale_series", "days_to_show"): "days before the show for this checkpoint",
}

SYNTH_JOIN_MAP = """\
- synth_resale_series.event_id = synth_event_demand.event_id (the only join)
- event ids match the REAL warehouse's dim_event.event_id, but this sandbox is
  self-contained — never mix it with the real dataset in one query"""

SYNTH_SEMANTIC_RULES = """\
1. EVERYTHING demand-related here is SYNTHETIC (simulated sellouts, resale prices) —
   every answer must say the data is synthetic. Event/venue/artist names are real.
2. Only SELECT statements over the two tables listed here; refuse anything else,
   including questions that need the real (non-synthetic) warehouse.
3. capacity_source and face_price_source tell you which inputs were real vs estimated —
   mention them when a question hinges on capacity or price levels."""

SYNTH_FEW_SHOTS: list[tuple[str, str]] = [
    (
        "Which sold-out shows have the highest resale markup?",
        """SELECT event_name, venue_name, show_date, resale_multiplier, resale_price_at_show
FROM {ds}.synth_event_demand
WHERE sold_out ORDER BY resale_multiplier DESC LIMIT 10""",
    ),
    (
        "What share of Bay Area shows sell out, by venue size band?",
        """SELECT CASE WHEN capacity < 500 THEN 'small (<500)'
            WHEN capacity < 2000 THEN 'mid (500-2000)' ELSE 'large (2000+)' END AS size_band,
       COUNT(*) AS shows, ROUND(AVG(CAST(sold_out AS INT64)) * 100, 1) AS pct_sold_out
FROM {ds}.synth_event_demand WHERE dma_code = '807'
GROUP BY size_band ORDER BY size_band""",
    ),
    (
        "How does the resale price for event rZ7HnEZ1Af00jd evolve toward the show?",
        """SELECT days_to_show, resale_price
FROM {ds}.synth_resale_series
WHERE event_id = 'rZ7HnEZ1Af00jd' ORDER BY days_to_show DESC""",
    ),
]


# ---------------------------------------------------------------------------
# Pure rendering (offline-tested in tests/test_build_schema_context.py)
# ---------------------------------------------------------------------------


def render_context(
    columns: list[dict[str, str]],
    stats: dict[str, dict[str, str]],
    project: str,
    dataset: str,
    as_of: str,
    *,
    allowed=None,
    table_notes=None,
    column_notes=None,
    join_map: str | None = None,
    semantic_rules: str | None = None,
    few_shots=None,
    intro: str | None = None,
    vocab: dict | None = None,
) -> str:
    """Render the full markdown context from schema rows + per-table stats.

    Defaults render the REAL warehouse profile; the synth profile passes its own
    allow-list/notes/rules/examples.
    """
    allowed = allowed if allowed is not None else ALLOWED_TABLES
    table_notes = table_notes if table_notes is not None else TABLE_NOTES
    column_notes = column_notes if column_notes is not None else COLUMN_NOTES
    join_map = join_map if join_map is not None else JOIN_MAP
    semantic_rules = semantic_rules if semantic_rules is not None else SEMANTIC_RULES
    few_shots = few_shots if few_shots is not None else FEW_SHOTS
    intro = intro if intro is not None else (
        "Bay-Area-centric live-music demand warehouse: Ticketmaster prices, Google Trends\n"
        "interest, YouTube stats, conformed dims, and a price forecast. Dates are UTC days."
    )

    tables: dict[str, list[dict[str, str]]] = {}
    for row in columns:
        tables.setdefault(row["table_name"], []).append(row)
    missing = allowed - tables.keys()
    if missing:
        raise SystemExit(f"allow-listed tables missing from INFORMATION_SCHEMA: {sorted(missing)}")
    unnoted = allowed - table_notes.keys()
    if unnoted:
        raise SystemExit(f"allow-listed tables missing a TABLE_NOTES entry: {sorted(unnoted)}")

    lines: list[str] = [
        "<!-- Generated by eda/build_schema_context.py — do not edit by hand. -->",
        f"<!-- As of {as_of}. Re-run the generator after any schema change. -->",
        "",
        f"# Warehouse: `{project}.{dataset}` (BigQuery, event-demand star schema)",
        "",
        intro,
        "",
        "## Semantic rules (non-negotiable)",
        "",
        semantic_rules,
        "",
        "## Join map",
        "",
        join_map,
    ]
    if vocab:
        lines += [
            "",
            "## Canonical values — copy these strings EXACTLY; never invent variants",
        ]
        if vocab.get("genres"):
            lines += ["", "primary_genre: " + " | ".join(vocab["genres"])]
            lines += ["", f"Genre aliases (user's term -> canonical): {GENRE_ALIASES}"]
        if vocab.get("statuses"):
            lines += ["", "status_code: " + " | ".join(vocab["statuses"])]
        if vocab.get("metros"):
            lines += ["", "Top metros by upcoming shows (filter by dma_code, quoted string):"]
            lines += [
                f"- '{m['dma_code']}' = {m['metro_name']} ({m['events']} upcoming)"
                for m in vocab["metros"]
            ]
    lines += [
        "",
        "## Tables",
    ]
    for name in sorted(tables, key=lambda t: (not t.startswith("fact_event"), t)):
        stat = stats.get(name, {})
        meta = f"{stat.get('row_count', '?')} rows"
        if stat.get("latest"):
            meta += f", latest {FACT_DATE_COLUMN} {stat['latest']}"
        lines += ["", f"### {name} ({meta})", "", table_notes[name], ""]
        for col in tables[name]:
            note = column_notes.get((name, col["column_name"]))
            suffix = f" — {note}" if note else ""
            lines.append(f"- `{col['column_name']}` {col['data_type']}{suffix}")

    lines += ["", "## Examples", ""]
    for question, sql in few_shots:
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


def fetch_columns(project: str, dataset: str, allowed=None) -> list[dict[str, str]]:
    allowed = allowed if allowed is not None else ALLOWED_TABLES
    names = ", ".join(f"'{t}'" for t in sorted(allowed))
    sql = (
        f"SELECT table_name, column_name, data_type "
        f"FROM `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMNS "
        f"WHERE table_name IN ({names}) ORDER BY table_name, ordinal_position"
    )
    return bq_rows(sql, project)


def fetch_stats(project: str, dataset: str, allowed=None) -> dict[str, dict[str, str]]:
    allowed = allowed if allowed is not None else ALLOWED_TABLES
    names = ", ".join(f"'{t}'" for t in sorted(allowed))
    counts = bq_rows(
        f"SELECT table_id, row_count FROM `{project}.{dataset}`.__TABLES__ "
        f"WHERE table_id IN ({names})",
        project,
    )
    stats = {r["table_id"]: {"row_count": r["row_count"]} for r in counts}
    dated = [t for t in sorted(allowed) if t.startswith("fact_")]
    union = " UNION ALL ".join(
        f"SELECT '{t}' AS t, CAST(MAX({FACT_DATE_COLUMN}) AS STRING) AS latest "
        f"FROM `{project}.{dataset}.{t}`"
        for t in dated
    )
    if union:
        for row in bq_rows(union, project):
            stats.setdefault(row["t"], {})["latest"] = row["latest"]
    return stats


def fetch_vocab(project: str, dataset: str, synth: bool = False) -> dict:
    """Canonical categorical values, live from the warehouse at generation time.

    Exists because the dominant real-user failure was the model INVENTING literals
    ('Electronic Dance Music (EDM)', a metro_name with a comma) — matching zero
    rows and answering "we track nothing". Deterministic given the warehouse.
    """
    if synth:
        genres = bq_rows(
            f"SELECT DISTINCT primary_genre AS g FROM `{project}.{dataset}.synth_event_demand` "
            f"WHERE primary_genre IS NOT NULL ORDER BY g",
            project,
        )
        return {"genres": [r["g"] for r in genres]}
    genres = bq_rows(
        f"SELECT DISTINCT primary_genre AS g FROM `{project}.{dataset}.dim_event` "
        f"WHERE primary_genre IS NOT NULL ORDER BY g",
        project,
    )
    statuses = bq_rows(
        f"SELECT DISTINCT status_code AS s FROM `{project}.{dataset}.fact_event_demand` "
        f"WHERE status_code IS NOT NULL ORDER BY s",
        project,
    )
    metros = bq_rows(
        f"SELECT v.dma_code, g.metro_name, COUNT(DISTINCT e.event_id) AS events "
        f"FROM `{project}.{dataset}.dim_event` e "
        f"JOIN `{project}.{dataset}.dim_venue` v ON e.venue_id = v.venue_id "
        f"JOIN `{project}.{dataset}.dim_geo` g ON v.dma_code = g.dma_code "
        f"WHERE e.show_date >= CURRENT_DATE() "
        f"GROUP BY v.dma_code, g.metro_name ORDER BY events DESC, v.dma_code LIMIT 12",
        project,
    )
    return {
        "genres": [r["g"] for r in genres],
        "statuses": [r["s"] for r in statuses],
        "metros": metros,
    }


def dry_run_examples(project: str, dataset: str, few_shots=None) -> None:
    """Compile every few-shot against the live schema; fail generation on error."""

    for question, sql in (few_shots if few_shots is not None else FEW_SHOTS):
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
    parser.add_argument("--dataset", default=None, help="defaults per profile")
    parser.add_argument("--output", type=Path, default=None, help="defaults per profile")
    parser.add_argument(
        "--synth", action="store_true",
        help="generate the synthetic-sandbox profile (event_demand_synth)",
    )
    parser.add_argument(
        "--skip-validation", action="store_true", help="skip the few-shot dry-run (offline)"
    )
    args = parser.parse_args()

    if args.synth:
        dataset = args.dataset or SYNTH_DATASET
        output = args.output or SYNTH_OUTPUT_PATH
        profile = dict(
            allowed=ALLOWED_TABLES_SYNTH, table_notes=SYNTH_TABLE_NOTES,
            column_notes=SYNTH_COLUMN_NOTES, join_map=SYNTH_JOIN_MAP,
            semantic_rules=SYNTH_SEMANTIC_RULES, few_shots=SYNTH_FEW_SHOTS,
            intro=(
                "SYNTHETIC sandbox over real events: simulated sellouts and resale prices\n"
                "(no real source provides these; TM bronze has 0.0% resale data). Every\n"
                "answer from this dataset must be labeled synthetic."
            ),
        )
    else:
        dataset = args.dataset or DEFAULT_DATASET
        output = args.output or OUTPUT_PATH
        profile = {}

    if not args.skip_validation:
        dry_run_examples(args.project, dataset, profile.get("few_shots"))
    text = render_context(
        fetch_columns(args.project, dataset, profile.get("allowed")),
        fetch_stats(args.project, dataset, profile.get("allowed")),
        args.project,
        dataset,
        utc_now_iso(),
        vocab=fetch_vocab(args.project, dataset, synth=args.synth),
        **profile,
    )
    output.write_text(text + "\n", encoding="utf-8")
    print(f"[build_schema_context] wrote {output} ({len(text)} chars, ~{len(text)//4} tokens)")


if __name__ == "__main__":
    main()
