#!/usr/bin/env python3
"""Deterministic sizing/health checks behind the collection-efficiency redesign.

Reproduces the numbers cited in ``docs/collection_efficiency_review.md`` and the
freshness table in ``docs/REPO_STATE.md`` so they can be re-checked identically
after more data lands (all queries read the live silver/gold tables via the
authenticated ``bq`` CLI; no LLM at runtime).

Sections (pick with flags, default = all):
  --freshness   MAX(snapshot_date) per fact table (pipeline liveness at a glance)
  --pricing     TM priceRanges coverage + priced-from-first-observation split
  --pairs       Trends targeting: upcoming headliner×DMA pair counts by tier
  --states      upcoming TM event volume by state (where sweep calls go)

Run (repo root, conda env ``music-demand`` or anything with the bq CLI authed):
    python eda/collection_sizing.py
    python eda/collection_sizing.py --freshness
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, bq_rows, fq, utc_now_iso  # noqa: E402

FACT_TABLES = ["tm_observations", "fact_trends", "fact_trends_daily",
               "fact_youtube", "fact_event_demand"]

# EDM match used across sections (primary_genre in dims; genre/subgenre in tm_events).
EDM_GENRE_SQL = "(LOWER({col}) LIKE '%dance%' OR LOWER({col}) LIKE '%electronic%')"
BAY_AREA_DMA = "807"  # Nielsen DMA: San Francisco-Oakland-San Jose


def _print_rows(title: str, rows: list[dict[str, str]]) -> None:
    print(f"\n== {title} ==")
    if not rows:
        print("  (no rows)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  " + "  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def freshness(project: str, dataset: str) -> None:
    sql = "\nUNION ALL ".join(
        f"SELECT '{t}' AS src, CAST(MAX(snapshot_date) AS STRING) AS latest, "
        f"COUNT(*) AS n FROM {fq(project, dataset, t)}"
        for t in FACT_TABLES
    ) + "\nORDER BY src"
    _print_rows("data freshness (MAX snapshot_date per fact table)", bq_rows(sql, project))


def pricing(project: str, dataset: str) -> None:
    obs = fq(project, dataset, "tm_observations")
    _print_rows("TM pricing coverage (observations)", bq_rows(
        f"SELECT COUNT(DISTINCT event_id) AS events, COUNT(*) AS obs, "
        f"ROUND(COUNTIF(price_min IS NOT NULL)/COUNT(*)*100, 1) AS pct_obs_priced, "
        f"COUNT(DISTINCT IF(price_min IS NOT NULL, event_id, NULL)) AS events_priced "
        f"FROM {obs}", project))
    _print_rows("priced-from-first-observation split (re-poll value test)", bq_rows(
        f"""
        WITH per_event AS (
          SELECT event_id, MIN(snapshot_date) AS first_seen,
                 MIN(IF(price_min IS NOT NULL, snapshot_date, NULL)) AS first_priced
          FROM {obs} GROUP BY event_id
        )
        SELECT COUNT(*) AS events,
               COUNTIF(first_priced IS NOT NULL) AS ever_priced,
               COUNTIF(first_priced = first_seen) AS priced_from_day1,
               COUNTIF(first_priced > first_seen) AS gained_price_later
        FROM per_event""", project))


def pairs(project: str, dataset: str) -> None:
    e, v, b = (fq(project, dataset, t) for t in ("dim_event", "dim_venue", "bridge_event_artist"))
    edm = EDM_GENRE_SQL.format(col="e.primary_genre")
    base = (
        f"FROM {e} e JOIN {v} v USING(venue_id) JOIN {b} b USING(event_id) "
        f"WHERE e.show_date >= CURRENT_DATE() AND b.is_headliner"
    )
    segs = {
        "all upcoming": "",
        "next 90 days": " AND e.show_date < DATE_ADD(CURRENT_DATE(), INTERVAL 90 DAY)",
        f"Bay Area (DMA {BAY_AREA_DMA})": f" AND v.dma_code='{BAY_AREA_DMA}'",
        "EDM nationwide": f" AND {edm}",
        f"tier-1 (Bay {BAY_AREA_DMA} OR EDM)": f" AND (v.dma_code='{BAY_AREA_DMA}' OR {edm})",
    }
    sql = "\nUNION ALL ".join(
        f"SELECT '{name}' AS seg, "
        f"COUNT(DISTINCT CONCAT(b.artist_id,'|',v.dma_code)) AS pairs, "
        f"COUNT(DISTINCT b.artist_id) AS artists, COUNT(DISTINCT e.event_id) AS events "
        f"{base}{cond}"
        for name, cond in segs.items()
    ) + "\nORDER BY seg"
    _print_rows("upcoming headliner×DMA pairs (Trends targeting tiers)", bq_rows(sql, project))


def states(project: str, dataset: str) -> None:
    _print_rows("upcoming TM events by state (top 15)", bq_rows(
        f"SELECT venue_state_code AS st, COUNT(*) AS upcoming, "
        f"COUNTIF(price_min IS NOT NULL) AS priced "
        f"FROM {fq(project, dataset, 'tm_events')} WHERE local_date >= CURRENT_DATE() "
        f"GROUP BY 1 ORDER BY upcoming DESC LIMIT 15", project))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    for flag in ("freshness", "pricing", "pairs", "states"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()

    picked = [f for f in ("freshness", "pricing", "pairs", "states") if getattr(args, f)]
    sections = picked or ["freshness", "pricing", "pairs", "states"]
    print(f"[collection_sizing] {utc_now_iso()} project={args.project} sections={sections}")
    for name in sections:
        globals()[name](args.project, args.dataset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
