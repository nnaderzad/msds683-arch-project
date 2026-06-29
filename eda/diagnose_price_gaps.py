#!/usr/bin/env python3
"""COLLECT-1 step 0 — classify WHY priced Ticketmaster shows have incomplete price series.

A price forecast needs a *continuous* daily price series per show, and Ticketmaster
price is forward-only: a day not captured is a permanent gap (there is no historical
price backfill). Before adding a priority re-fetch-by-id queue to the collector, this
script proves the gaps are actually fixable by re-fetching — i.e. they are COVERAGE
gaps (the daily sweep missed an event that was in the feed) rather than SOURCE gaps
(the event was captured but Ticketmaster returned no price, which a re-fetch cannot fix).

For each priced event (>=1 day with a price) over the snapshot window:
  * present days   = snapshot dates the event appears at all (a row in fact_ticketmaster)
  * priced days    = present days that carry a price
  * INTERIOR-absent days = window dates between the event's first and last appearance
    that are absent -> unambiguous sweep drops (it was in the feed before AND after), so
    a daily re-fetch-by-id WOULD have captured them. **Coverage gap; re-fetch fixes.**
  * unpriced-present days = present but no price -> Ticketmaster gave no price, so a
    re-fetch CANNOT add it. **Source gap; not fixable.**
  * edge-absent days (before first / after last appearance) = entry/exit of the feed
    (announced late, show passed, removed) -> ambiguous, not counted as fixable.

Headline: how much of the incompleteness is interior coverage (re-fetch fixes) vs
unpriced-present source (re-fetch can't). If interior coverage is material, the priority
re-fetch queue has clear ROI; if it is essentially all source gaps, narrow the promise
to "capture every day a price exists" and say so.

Deterministic + re-runnable: queries the persistent ``fact_ticketmaster`` dbt model via
the already-authenticated ``bq`` CLI; the classifier is a pure function unit-tested
offline. No LLM at runtime.

Run (repo root, bq authed for the project):
    python eda/diagnose_price_gaps.py
    python eda/diagnose_price_gaps.py --dataset event_demand_analytics
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, bq_rows, utc_now_iso  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
FACT_TABLE = "fact_ticketmaster"


# ---------------------------------------------------------------------------
# BigQuery I/O (thin; the classifier below stays pure + offline-testable)
# ---------------------------------------------------------------------------

def load_presence_series(project: str, dataset: str,
                         table: str = FACT_TABLE) -> list[dict[str, str]]:
    """One row per (priced event, snapshot_date): does it carry a price that day?

    Restricted to events with >=1 priced day — the universe a price re-fetch can help.
    ``table`` lets the same classifier run against the forward-filled ``fact_ticketmaster``
    (expect 0 interior/trailing gaps — the artifact) or the honest ``tm_observations``
    (expect real gaps — the proof the forward-fill is gone). Both share the
    (event_id, snapshot_date, price_min) grain.
    """

    sql = f"""
        WITH priced AS (
          SELECT event_id
          FROM `{project}.{dataset}.{table}`
          GROUP BY event_id
          HAVING LOGICAL_OR(price_min IS NOT NULL)
        )
        SELECT f.event_id,
               CAST(f.snapshot_date AS STRING) AS snapshot_date,
               LOGICAL_OR(f.price_min IS NOT NULL) AS has_price
        FROM `{project}.{dataset}.{table}` AS f
        JOIN priced USING (event_id)
        GROUP BY f.event_id, f.snapshot_date
    """
    return bq_rows(sql, project)


# ---------------------------------------------------------------------------
# Pure classifier (unit-tested offline; no network)
# ---------------------------------------------------------------------------

def window_dates(rows: list[dict]) -> list[str]:
    """Sorted distinct snapshot dates present anywhere in the panel."""

    return sorted({r["snapshot_date"] for r in rows})


def _is_true(v: object) -> bool:
    return str(v).strip().lower() == "true"


def classify_event_gaps(rows: list[dict], all_dates: list[str]) -> list[dict]:
    """Per event, split missing days into interior-coverage vs source vs edge gaps.

    ``rows`` are (event_id, snapshot_date, has_price) presence rows; ``all_dates`` is the
    sorted distinct snapshot window. Returns one classification row per event.
    """

    dates_sorted = sorted(all_dates)
    by_event: dict[str, dict[str, bool]] = {}
    for r in rows:
        by_event.setdefault(r["event_id"], {})[r["snapshot_date"]] = _is_true(r.get("has_price"))

    out: list[dict] = []
    for event_id, day_price in by_event.items():
        present = sorted(day_price)
        if not present:
            continue
        first, last = present[0], present[-1]
        present_set = set(present)
        interior_absent = sum(1 for d in dates_sorted if first < d < last and d not in present_set)
        leading_absent = sum(1 for d in dates_sorted if d < first)
        trailing_absent = sum(1 for d in dates_sorted if d > last)
        priced_days = sum(1 for v in day_price.values() if v)
        unpriced_present = len(present) - priced_days
        out.append({
            "event_id": event_id,
            "present_days": len(present),
            "priced_days": priced_days,
            "interior_absent": interior_absent,     # coverage gap — re-fetch fixes
            "unpriced_present": unpriced_present,    # source gap — re-fetch can't
            "leading_absent": leading_absent,        # edge: entered the feed late
            "trailing_absent": trailing_absent,      # edge: left the feed / show passed
            "first_present": first,
            "last_present": last,
        })
    out.sort(key=lambda c: (-c["interior_absent"], -c["unpriced_present"], c["event_id"]))
    return out


def summarize_gaps(classified: list[dict], total_days: int) -> dict:
    """Aggregate the coverage-vs-source split across the priced universe."""

    n = len(classified)
    interior = sum(c["interior_absent"] for c in classified)
    source = sum(c["unpriced_present"] for c in classified)
    leading = sum(c["leading_absent"] for c in classified)
    trailing = sum(c["trailing_absent"] for c in classified)
    fixable_events = sum(1 for c in classified if c["interior_absent"] > 0)
    source_only_events = sum(
        1 for c in classified if c["interior_absent"] == 0 and c["unpriced_present"] > 0)
    coverage_plus_source = interior + source
    return {
        "priced_events": n,
        "total_days": total_days,
        "interior_coverage_gap_days": interior,
        "unpriced_present_source_gap_days": source,
        "leading_edge_days": leading,
        "trailing_edge_days": trailing,
        "pct_gap_days_fixable_by_refetch": (
            round(100 * interior / coverage_plus_source, 1) if coverage_plus_source else 0.0),
        "events_with_interior_gap": fixable_events,
        "events_source_gap_only": source_only_events,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_PER_EVENT_FIELDS = [
    "event_id", "present_days", "priced_days", "interior_absent",
    "unpriced_present", "leading_absent", "trailing_absent",
    "first_present", "last_present",
]


def write_outputs(out_dir: Path, classified: list[dict], summary: dict, as_of: str,
                  table: str = FACT_TABLE) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"tm_price_gap_diagnosis_{table}"
    with (out_dir / f"{stem}_by_event.csv").open(
            "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_PER_EVENT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(classified)

    s = summary
    lines = [
        f"# Ticketmaster price-gap diagnosis — `{table}`",
        "",
        f"_Generated by `eda/diagnose_price_gaps.py --table {table}`, as of {as_of}, over "
        f"{s['total_days']} daily snapshots. Re-run to refresh._",
        "",
        "Classifies why **priced** shows have incomplete daily price series, to size the "
        "ROI of a priority re-fetch-by-id queue (which fixes coverage gaps but not source "
        "gaps).",
        "",
        f"- **{s['priced_events']:,}** events carry a price on >=1 day (the re-fetch universe).",
        f"- **{s['interior_coverage_gap_days']:,}** interior coverage-gap days — event in the "
        "feed before AND after, missing in between -> **a daily re-fetch would capture these**.",
        f"- **{s['unpriced_present_source_gap_days']:,}** unpriced-present source-gap days — "
        "captured but Ticketmaster returned no price -> **re-fetch cannot fix**.",
        f"- **{s['pct_gap_days_fixable_by_refetch']}%** of (coverage + source) gap days are "
        "fixable by re-fetch.",
        f"- **{s['events_with_interior_gap']:,}** events have >=1 interior coverage gap "
        "(re-fetch helps); **{:,}** are source-gap-only (re-fetch won't).".format(
            s["events_source_gap_only"]),
        f"- Edge days (ambiguous, not counted): {s['leading_edge_days']:,} leading / "
        f"{s['trailing_edge_days']:,} trailing.",
        "",
    ]
    (out_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--table", default=FACT_TABLE,
                        help="price-history table to diagnose (fact_ticketmaster = the "
                             "forward-filled artifact; tm_observations = the honest panel)")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    as_of = utc_now_iso()
    rows = load_presence_series(args.project, args.dataset, args.table)
    if not rows:
        print(f"[diagnose_price_gaps] no rows in {args.table}", file=sys.stderr)
        return 1
    dates = window_dates(rows)
    classified = classify_event_gaps(rows, dates)
    summary = summarize_gaps(classified, len(dates))

    write_outputs(Path(args.output_dir), classified, summary, as_of, args.table)
    print(f"[diagnose_price_gaps] {summary['priced_events']:,} priced events over "
          f"{summary['total_days']} days; "
          f"{summary['interior_coverage_gap_days']:,} interior coverage-gap days "
          f"({summary['pct_gap_days_fixable_by_refetch']}% of gaps fixable by re-fetch), "
          f"{summary['unpriced_present_source_gap_days']:,} source-gap days; "
          f"{summary['events_with_interior_gap']:,} events helped by re-fetch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
