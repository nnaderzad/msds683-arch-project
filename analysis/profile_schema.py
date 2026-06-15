#!/usr/bin/env python3
"""Profile the REAL warehouse to back the midterm schema decision with numbers.

This is decision-support, not a pipeline step: it reads the live Ticketmaster
silver table (BigQuery) plus the committed Trends roster + geo crosswalks, and
reports the few facts that actually decide the schema:

  - rows-per-snapshot for each candidate grain (event x day / artist x date /
    artist x DMA x date) -> how big each fact table is, and whether one shared
    grain is even possible (it is not -> argues for the constellation);
  - price coverage overall and by genre -> verifies the "~33%" claim (it is 23%)
    and shows EDM is the best-covered genre (the scope argument);
  - TM attraction <-> Trends roster name-join hit-rate -> can we actually join
    events to the local-interest signal on artist name;
  - venue -> DMA crosswalk coverage (reusing google_trends_api.geo_lookup) ->
    can we attach a metro/DMA to each event for the (artist, DMA, date) join.

Deterministic + re-runnable: no synthetic data, no LLM at runtime. Numbers move
only as the real snapshots accumulate, so every row is stamped with as_of_ts and
the table row count it was computed against.

Run (repo root, music-demand env, `bq` authed for the project):
    python analysis/profile_schema.py
    python analysis/profile_schema.py --project data-architecture-498123

Writes analysis/output/schema_profile.csv (tidy long) + schema_profile.md
(summary the schema_decision.md doc embeds).
"""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Reuse the production venue -> DMA resolver instead of re-deriving the crosswalk.
sys.path.insert(0, str(REPO_ROOT / "google_trends_api"))
from geo_lookup import GeoLookup  # noqa: E402

DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_DATASET = "event_demand_analytics"
TM_TABLE = "tm_events"
SAMPLE_DIR = REPO_ROOT / "google_trends_api" / "sample_data"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# EDM is the focal genre: it has the geo/Trends backbone AND the best price
# coverage. Ticketmaster files electronic acts under these classification genres.
EDM_GENRES = ("Dance/Electronic", "Electronic")


def bq_rows(sql: str, project: str) -> list[dict[str, str]]:
    """Run a BigQuery SQL statement via the bq CLI and return rows as dicts.

    Uses the already-authenticated `bq` CLI (CSV output) so the profiler needs
    no extra Python client dependency and runs wherever the project is authed.
    """

    proc = subprocess.run(
        ["bq", f"--project_id={project}", "query", "--use_legacy_sql=false",
         "--format=csv", "--max_rows=100000", sql],
        capture_output=True, text=True, check=True,
    )
    return list(csv.DictReader(io.StringIO(proc.stdout)))


def fq(project: str, dataset: str, table: str) -> str:
    return f"`{project}.{dataset}.{table}`"


# ---------------------------------------------------------------------------
# Individual profiles — each returns a list of (metric, dimension, value, unit)
# ---------------------------------------------------------------------------

def profile_grains(project: str, dataset: str) -> list[tuple[str, str, str, str]]:
    """Rows-per-snapshot for each candidate fact grain (the constellation case)."""

    tm = fq(project, dataset, TM_TABLE)
    row = bq_rows(
        f"SELECT COUNT(*) AS n_events, "
        f"COUNT(DISTINCT venue_id) AS n_venues, "
        f"COUNT(DISTINCT attraction_names) AS n_attr_combos "
        f"FROM {tm}",
        project,
    )[0]

    # Trends/YouTube facts have no silver table yet, so size them from the
    # committed roster: the acts + artist-DMA targets the collectors track.
    artists = _read_csv(SAMPLE_DIR / "roster_artist_2026-06-14.csv")
    selected = [a for a in artists if a.get("selected", "").strip().lower() == "true"]
    targets = _read_csv(SAMPLE_DIR / "roster_targets_2026-06-14.csv")
    n_artists = len(selected) or len(artists)
    n_target_pairs = len(targets)

    return [
        ("grain_rows_per_snapshot", "fact_event_price_snapshot (event x day)",
         row["n_events"], "rows/day"),
        ("grain_rows_per_snapshot", "fact_artist_popularity_daily (artist x day)",
         str(n_artists), "rows/day"),
        ("grain_rows_per_snapshot", "fact_artist_interest_daily (artist x DMA x day)",
         str(n_target_pairs), "rows/day (targeted pairs)"),
        ("dim_size", "dim_venue (distinct venue_id)", row["n_venues"], "rows"),
        ("dim_size", "dim_artist (distinct attraction combos, raw)",
         row["n_attr_combos"], "rows (pre-split)"),
    ]


def profile_price_coverage(project: str, dataset: str) -> list[tuple[str, str, str, str]]:
    """Overall + per-genre share of events that expose a Ticketmaster price."""

    tm = fq(project, dataset, TM_TABLE)
    overall = bq_rows(
        f"SELECT ROUND(100*COUNTIF(price_min IS NOT NULL)/COUNT(*),1) AS pct, "
        f"COUNT(*) AS n FROM {tm}",
        project,
    )[0]
    out = [("price_coverage", "all genres", overall["pct"], "% events with price")]

    by_genre = bq_rows(
        f"SELECT genre, COUNT(*) AS n, "
        f"ROUND(100*COUNTIF(price_min IS NOT NULL)/COUNT(*),1) AS pct "
        f"FROM {tm} GROUP BY genre ORDER BY n DESC LIMIT 12",
        project,
    )
    for r in by_genre:
        out.append(("price_coverage", f"genre={r['genre']} (n={r['n']})",
                    r["pct"], "% events with price"))
    return out


def profile_join_hitrate(project: str, dataset: str) -> list[tuple[str, str, str, str]]:
    """How many events have a Trends interest signal *today*.

    The roster is the Trends **collection-priority queue** (whom the rate-limited
    API fetches first), NOT a modeling filter — the target is every artist with a
    show, and an event missing a signal gets NULL interest, never dropped. So this
    measures current collection coverage, which grows as the roster expands.

    attraction_names is pipe-delimited (festivals carry many acts), so an event
    has a signal if ANY of its attractions is currently in the roster.
    """

    tm = fq(project, dataset, TM_TABLE)
    roster = {
        a["artist"].strip().lower()
        for a in _read_csv(SAMPLE_DIR / "roster_artist_2026-06-14.csv")
        if a.get("artist")
    }

    out: list[tuple[str, str, str, str]] = []
    for label, where in (("all genres", ""),
                         ("EDM (Dance/Electronic)",
                          f"WHERE genre IN {EDM_GENRES!r}".replace("'", '"'))):
        rows = bq_rows(
            f"SELECT attraction_names FROM {tm} {where}",
            project,
        )
        events = [r["attraction_names"] for r in rows if r.get("attraction_names")]
        matched = sum(
            1 for names in events
            if any(part.strip().lower() in roster for part in names.split("|"))
        )
        pct = round(100 * matched / len(events), 1) if events else 0.0
        out.append(("interest_coverage_today", f"{label} (n={len(events)})",
                    str(pct), "% events with a Trends signal now (starter roster)"))
    out.append(("roster_size", "starter roster artists (collection queue)",
                str(len(roster)), "artists"))
    return out


def profile_dma_coverage(project: str, dataset: str) -> list[tuple[str, str, str, str]]:
    """Share of events whose venue resolves to a DMA (the (artist,DMA,date) key).

    Resolves distinct (postal, state) pairs once via the production GeoLookup and
    weights by the event count behind each pair, so the % is event-weighted.
    """

    tm = fq(project, dataset, TM_TABLE)
    geo = GeoLookup()
    out: list[tuple[str, str, str, str]] = []
    for label, where in (("all genres", ""),
                         ("EDM (Dance/Electronic)",
                          f"WHERE genre IN {EDM_GENRES!r}".replace("'", '"'))):
        rows = bq_rows(
            f"SELECT venue_postal_code AS zip, venue_state_code AS st, "
            f"COUNT(*) AS n FROM {tm} {where} GROUP BY zip, st",
            project,
        )
        total = resolved_zip = resolved_any = 0
        for r in rows:
            n = int(r["n"])
            total += n
            hit = geo.resolve(zip_code=r["zip"], state=r["st"])
            if hit and hit.method == "zip":
                resolved_zip += n
            if hit:
                resolved_any += n
        out.append(("dma_coverage_zip", f"{label} (n={total})",
                    str(round(100 * resolved_zip / total, 1) if total else 0.0),
                    "% events resolved by ZIP"))
        out.append(("dma_coverage_any", f"{label} (n={total})",
                    str(round(100 * resolved_any / total, 1) if total else 0.0),
                    "% events resolved (ZIP or state fallback)"))
    return out


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(project: str, dataset: str) -> list[dict[str, str]]:
    """Run every profile and return tidy long records stamped with provenance."""

    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_total = bq_rows(f"SELECT COUNT(*) AS n FROM {fq(project, dataset, TM_TABLE)}",
                      project)[0]["n"]

    records: list[dict[str, str]] = []
    for profile in (profile_grains, profile_price_coverage,
                    profile_join_hitrate, profile_dma_coverage):
        for metric, dimension, value, unit in profile(project, dataset):
            records.append({
                "metric": metric, "dimension": dimension, "value": value,
                "unit": unit, "as_of_ts": as_of, "tm_events_rows": n_total,
            })
    return records


def write_outputs(records: list[dict[str, str]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = OUTPUT_DIR / "schema_profile.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["metric", "dimension", "value", "unit",
                            "as_of_ts", "tm_events_rows"])
        writer.writeheader()
        writer.writerows(records)

    md_path = OUTPUT_DIR / "schema_profile.md"
    as_of = records[0]["as_of_ts"] if records else ""
    n_total = records[0]["tm_events_rows"] if records else ""
    lines = [
        "# Schema profile — real-warehouse evidence",
        "",
        f"_Generated by `analysis/profile_schema.py`, as of {as_of} "
        f"against {n_total} `tm_events` rows. Re-run to refresh; numbers move "
        "only as snapshots accumulate._",
        "",
        "| metric | dimension | value | unit |",
        "|---|---|---|---|",
    ]
    lines += [f"| {r['metric']} | {r['dimension']} | {r['value']} | {r['unit']} |"
              for r in records]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[profile] wrote {csv_path}")
    print(f"[profile] wrote {md_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    args = parser.parse_args()

    records = run(args.project, args.dataset)
    write_outputs(records)
    for r in records:
        print(f"  {r['metric']:24} {r['dimension']:48} {r['value']:>8} {r['unit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
