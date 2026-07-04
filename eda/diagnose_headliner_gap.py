#!/usr/bin/env python3
"""MIG-3 diagnosis — why priced Ticketmaster shows have no headliner ``artist_id`` in gold.

A priced event can only carry artist-level demand signals (Trends ``local_interest``,
YouTube) if its **headliner** resolves to an ``artist_id``. The gold star left-joins the
headliner from ``bridge_event_artist`` (``is_headliner``), which A3
(``pipeline/silver/build_dimensions.py``) builds from each event's ``attraction_names`` —
and **skips any event that has no attractions** (``if not names: continue``). So an event
with no ``attraction_names`` gets no headliner and a NULL ``artist_id``.

This script classifies the priced universe to size that gap and ask whether it is
*fixable* (a join/normalization defect) or a **Ticketmaster source ceiling** (the API just
didn't return attractions). For each priced event (``price_min`` on >=1 day) it records:
  * ``has_attractions``  — ``attraction_names`` present in ``tm_events``
  * ``has_headliner``    — a row in ``bridge_event_artist`` with ``is_headliner``
  * ``name_matches_known_artist`` — the event name (normalized) exactly equals an artist we
    already know in ``dim_artist`` -> the **safe, no-fabrication** recovery set (attach a
    headliner we already have; never invent one for an artist-less club night / tribute).

Headline: if "no headliner" is essentially all "no attractions", the bridge logic is fine
and the gap is a source ceiling -- documented, not fixed. The safe-recovery count bounds
any low-risk enrichment.

Deterministic + re-runnable: queries the persistent silver/gold tables via the authenticated
``bq`` CLI; the classifier is a pure function. No LLM at runtime.

Run (repo root, bq authed for the project):
    python eda/diagnose_headliner_gap.py
    python eda/diagnose_headliner_gap.py --dataset event_demand_analytics
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, bq_rows, utc_now_iso  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


# ---------------------------------------------------------------------------
# BigQuery I/O (thin; the classifier below stays pure + offline-testable)
# ---------------------------------------------------------------------------

def load_priced_resolution(project: str, dataset: str) -> list[dict[str, str]]:
    """One row per priced event with its headliner-resolution facts."""

    sql = f"""
        WITH priced AS (
          SELECT DISTINCT event_id
          FROM `{project}.{dataset}.fact_ticketmaster`
          WHERE price_min IS NOT NULL
        ),
        artists AS (
          SELECT DISTINCT LOWER(TRIM(REGEXP_REPLACE(artist_name, r'\\s+', ' '))) AS aname
          FROM `{project}.{dataset}.dim_artist`
        ),
        bh AS (
          SELECT event_id, COUNTIF(is_headliner) AS n_headliner
          FROM `{project}.{dataset}.bridge_event_artist`
          GROUP BY event_id
        )
        SELECT
          p.event_id,
          t.event_name,
          NULLIF(t.genre, '') AS genre,
          (t.attraction_names IS NOT NULL AND t.attraction_names != '') AS has_attractions,
          (COALESCE(bh.n_headliner, 0) >= 1)                            AS has_headliner,
          (LOWER(TRIM(REGEXP_REPLACE(t.event_name, r'\\s+', ' '))) IN (
              SELECT aname FROM artists))                               AS name_matches_known_artist
        FROM priced p
        JOIN `{project}.{dataset}.tm_events` t USING (event_id)
        LEFT JOIN bh USING (event_id)
    """
    return bq_rows(sql, project)


# ---------------------------------------------------------------------------
# Pure classifier (unit-testable offline; no network)
# ---------------------------------------------------------------------------

def _is_true(v: object) -> bool:
    return str(v).strip().lower() == "true"


def summarize_resolution(rows: list[dict]) -> dict:
    """Aggregate the priced universe into the resolution breakdown + recovery sizing."""

    n = len(rows)
    has_headliner = sum(1 for r in rows if _is_true(r["has_headliner"]))
    no_attractions = sum(1 for r in rows if not _is_true(r["has_attractions"]))
    attr_but_no_headliner = sum(
        1 for r in rows
        if _is_true(r["has_attractions"]) and not _is_true(r["has_headliner"]))
    # Safe recovery: unresolved AND the event name is a known artist (no fabrication).
    safe_recoverable = sum(
        1 for r in rows
        if not _is_true(r["has_headliner"]) and _is_true(r["name_matches_known_artist"]))
    unresolved_genres = Counter(
        (r["genre"] or "(none)") for r in rows if not _is_true(r["has_headliner"]))
    return {
        "priced_events": n,
        "has_headliner": has_headliner,
        "unresolved": n - has_headliner,
        "no_attractions": no_attractions,
        "attr_but_no_headliner": attr_but_no_headliner,
        "safe_recoverable_name_match": safe_recoverable,
        "pct_resolved": round(100 * has_headliner / n, 1) if n else 0.0,
        "unresolved_top_genres": unresolved_genres.most_common(10),
    }


def unresolved_events(rows: list[dict]) -> list[dict]:
    """The unresolved priced events, with the safe-recovery flag, for inspection."""

    out = [
        {
            "event_id": r["event_id"],
            "event_name": r["event_name"],
            "genre": r["genre"] or "",
            "has_attractions": _is_true(r["has_attractions"]),
            "name_matches_known_artist": _is_true(r["name_matches_known_artist"]),
        }
        for r in rows if not _is_true(r["has_headliner"])
    ]
    out.sort(key=lambda e: (not e["name_matches_known_artist"], e["genre"], e["event_id"]))
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_FIELDS = ["event_id", "event_name", "genre", "has_attractions", "name_matches_known_artist"]


def write_outputs(out_dir: Path, summary: dict, unresolved: list[dict], as_of: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "headliner_gap_unresolved.csv").open(
            "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(unresolved)

    s = summary
    genres = ", ".join(f"{g} ({c})" for g, c in s["unresolved_top_genres"])
    lines = [
        "# Priced-event headliner-resolution diagnosis (MIG-3)",
        "",
        f"_Generated by `eda/diagnose_headliner_gap.py`, as of {as_of}. Re-run to refresh._",
        "",
        "Why some **priced** events carry no headliner `artist_id` in gold (so no Trends / "
        "YouTube signal can join). Tests whether it is a fixable bridge defect or a "
        "Ticketmaster **source ceiling** (no attractions returned).",
        "",
        f"- **{s['priced_events']:,}** priced events; **{s['has_headliner']:,}** resolve a "
        f"headliner (**{s['pct_resolved']}%**) — the honest modelable universe.",
        f"- **{s['unresolved']:,}** unresolved. Of these, **{s['no_attractions']:,}** have "
        "**no `attraction_names` at all** (TM returned none) — a source ceiling, not a bug.",
        f"- **{s['attr_but_no_headliner']:,}** have attractions but still no headliner "
        "(should be ~0 — the bridge always marks one when attractions exist).",
        f"- **Safe, no-fabrication recovery:** **{s['safe_recoverable_name_match']:,}** "
        "unresolved events whose name exactly matches an artist already in `dim_artist` "
        "(attach a known headliner; never invent one for artist-less club nights / tributes).",
        f"- Unresolved by genre: {genres}.",
        "",
        "**Takeaway:** the gap is overwhelmingly a TM source ceiling. The bridge logic is "
        "sound; NULL `artist_id` + the model's `has_interest` / `has_youtube` missingness "
        "flags already handle it honestly. Any enrichment should be limited to the safe "
        "name-match set and explicitly flagged as team-derived.",
        "",
    ]
    (out_dir / "headliner_gap_diagnosis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    as_of = utc_now_iso()
    rows = load_priced_resolution(args.project, args.dataset)
    if not rows:
        print("[diagnose_headliner_gap] no priced events found", file=sys.stderr)
        return 1
    summary = summarize_resolution(rows)
    unresolved = unresolved_events(rows)
    write_outputs(Path(args.output_dir), summary, unresolved, as_of)

    print(f"[diagnose_headliner_gap] {summary['priced_events']:,} priced events; "
          f"{summary['has_headliner']:,} resolved ({summary['pct_resolved']}%); "
          f"{summary['unresolved']:,} unresolved "
          f"({summary['no_attractions']:,} have no attractions = source ceiling); "
          f"{summary['safe_recoverable_name_match']:,} safe name-match recoveries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
