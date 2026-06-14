#!/usr/bin/env python3
"""Build the Google Trends roster from the Ticketmaster silver table.

Reads upcoming events from BigQuery ``tm_events`` (the TM pipeline's silver table),
explodes the pipe-delimited attraction list into individual artists, maps each
event's venue to a Google Trends DMA (``geo_lookup``), and aggregates to:

  - per ARTIST:      #events, #DMAs (markets), soonest show, top genre, a touring
                     score, rank, and a ``selected`` flag (top-N plus the curated
                     EDM/pop seed in ``config.ARTISTS``).
  - per (ARTIST,DMA): the targeted per-metro fetch queue for selected artists.

Ranking favors real touring acts over single-venue residencies/tributes: distinct
markets (DMAs) dominate, then event volume. The curated seed (``config.ARTISTS``)
is always selected and carries ``query`` overrides that disambiguate common names
(e.g. Fisher -> "Fisher DJ"). Backfill order is soonest-show-first (see
``soonest_*`` columns), independent of selection.

Artifacts (date-stamped for reproducibility):
  - sample_data/roster_artist_<date>.csv    (committed sample — top rows)
  - sample_data/roster_targets_<date>.csv   (committed sample — top rows)
  - data/google_trends/roster/<date>/*.csv  (full, gitignored)

Run (from repo root, in the music-demand env):
    python google_trends_api/build_roster.py --top-n 250
    python google_trends_api/build_roster.py --top-n 50 --states CA   # quick smoke
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from google.cloud import bigquery

import config
from geo_lookup import GeoLookup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_roster")

PROJECT_ID = "data-architecture-498123"
TM_TABLE = f"{PROJECT_ID}.event_demand_analytics.tm_events"

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = Path(__file__).with_name("sample_data")
SAMPLE_ROWS = 50

# Normalized seed name -> config.Artist (for query overrides + guaranteed selection).
_SEED = {" ".join(a.name.split()).lower(): a for a in config.ARTISTS}


def norm_name(name: str) -> str:
    """Collapse whitespace; the display key we group artists on."""
    return " ".join(str(name).split()).strip()


def query_for(artist: str) -> str:
    """The exact term to send to Google Trends (seed override or the name)."""
    seed = _SEED.get(artist.lower())
    return seed.query if seed else artist


def fetch_upcoming(states: list[str] | None) -> list[bigquery.table.Row]:
    """Pull one row per (artist, event) for upcoming shows from tm_events."""

    where = ["local_date >= CURRENT_DATE()", "attraction_names IS NOT NULL",
             "attraction_names != ''", "TRIM(a) != ''"]
    if states:
        where.append("venue_state_code IN UNNEST(@states)")
    sql = f"""
        SELECT TRIM(a) AS artist, venue_postal_code AS zip,
               venue_state_code AS state, local_date, genre
        FROM `{TM_TABLE}`, UNNEST(SPLIT(attraction_names, '|')) AS a
        WHERE {' AND '.join(where)}
    """
    params = []
    if states:
        params.append(bigquery.ArrayQueryParameter("states", "STRING", states))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    client = bigquery.Client(project=PROJECT_ID)
    return list(client.query(sql, job_config=job_config).result())


def build_frames(
    rows: list[bigquery.table.Row], geo: GeoLookup, top_n: int
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Aggregate exploded (artist, event) rows into artist + (artist, dma) frames."""

    records, unresolved = [], 0
    for r in rows:
        hit = geo.resolve(zip_code=r["zip"], state=r["state"])
        if hit is None:
            unresolved += 1
            continue
        records.append(
            {
                "artist": norm_name(r["artist"]),
                "dma": hit.dma,
                "geo_code": hit.geo_code,
                "dma_name": hit.dma_name,
                "local_date": r["local_date"],
                "genre": r["genre"],
            }
        )
    df = pd.DataFrame.from_records(records)

    # per (artist, dma) — the targeted fetch queue
    targets = (
        df.groupby(["artist", "dma", "geo_code", "dma_name"], as_index=False)
        .agg(n_events_in_dma=("local_date", "size"),
             soonest_in_dma=("local_date", "min"))
    )

    # per artist — for ranking / selection
    def top_genre(s: pd.Series) -> str | None:
        vals = [g for g in s if g]
        return Counter(vals).most_common(1)[0][0] if vals else None

    artist = (
        df.groupby("artist", as_index=False)
        .agg(n_events=("local_date", "size"),
             n_dmas=("dma", "nunique"),
             soonest_date=("local_date", "min"),
             top_genre=("genre", top_genre))
    )
    artist["query"] = artist["artist"].map(query_for)
    artist["in_seed"] = artist["artist"].str.lower().isin(_SEED)
    # Touring score: markets dominate event volume; residencies (1 DMA) rank low.
    artist["score"] = artist["n_dmas"] * 100 + artist["n_events"]
    artist = artist.sort_values(
        ["score", "soonest_date"], ascending=[False, True]
    ).reset_index(drop=True)
    artist["rank"] = artist.index + 1
    artist["selected"] = (artist["rank"] <= top_n) | artist["in_seed"]

    selected_names = set(artist.loc[artist["selected"], "artist"])
    targets = targets[targets["artist"].isin(selected_names)].copy()
    targets["query"] = targets["artist"].map(query_for)
    targets = targets.sort_values(["soonest_in_dma", "artist", "dma"]).reset_index(drop=True)

    artist = artist[
        ["rank", "artist", "query", "n_events", "n_dmas", "soonest_date",
         "top_genre", "score", "in_seed", "selected"]
    ]
    targets = targets[
        ["artist", "query", "dma", "geo_code", "dma_name",
         "n_events_in_dma", "soonest_in_dma"]
    ]
    stats = {
        "rows_in": len(rows),
        "venues_unresolved": unresolved,
        "artists_total": len(artist),
        "artists_selected": int(artist["selected"].sum()),
        "targets_selected": len(targets),
    }
    return artist, targets, stats


def write_outputs(artist: pd.DataFrame, targets: pd.DataFrame, run_date: str) -> None:
    full_dir = REPO_ROOT / "data" / "google_trends" / "roster" / run_date
    full_dir.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    for name, frame in (("artist", artist), ("targets", targets)):
        frame.to_csv(full_dir / f"roster_{name}.csv", index=False)
        frame.head(SAMPLE_ROWS).to_csv(
            SAMPLE_DIR / f"roster_{name}_{run_date}.csv", index=False
        )
    logger.info("Full roster -> %s ; samples -> %s", full_dir, SAMPLE_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=250,
                        help="How many top-ranked artists to select (default 250).")
    parser.add_argument("--states", nargs="*", default=None,
                        help="Limit to these state codes (smoke testing).")
    args = parser.parse_args()

    run_date = datetime.now(timezone.utc).date().isoformat()
    logger.info("Querying upcoming events from %s ...", TM_TABLE)
    rows = fetch_upcoming(args.states)
    geo = GeoLookup()
    artist, targets, stats = build_frames(rows, geo, args.top_n)
    write_outputs(artist, targets, run_date)

    logger.info("Summary: %s", stats)
    logger.info("Top 12 selected touring artists:\n%s",
                artist.head(12).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
