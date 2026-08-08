#!/usr/bin/env python3
"""Generate the synthetic demand layer into the `event_demand_synth` dataset.

Applies the seeded heuristics in ``synth/heuristics.py`` to every REAL event in
the warehouse (the "hybrid" design: real spine — events, artists, venues,
researched capacities, observed prices — synthetically infilled ONLY where no
source exists: sellout timing and the resale market, which Ticketmaster never
exposes; measured 0.0% resale priceRanges in bronze).

Outputs (both WRITE_TRUNCATE, provenance-stamped with ``synth_run_id``,
``generator_version``, ``seed``):

  * ``event_demand_synth.synth_event_demand``  — one row per event: capacity
    (researched, else labeled ``tier_estimate``), popularity inputs, demand
    ratio, sellout verdict + date, face price (observed, else labeled
    ``genre_median``), resale multiplier + price at show time.
  * ``event_demand_synth.synth_resale_series`` — resale price trajectory per
    event over days-to-show checkpoints (face at onsale → final multiplier).

Determinism: the base ``--seed`` plus a per-event SHA-256-derived stream means
the same seed reproduces the identical world regardless of row order or
partial reruns. NEVER writes to the honest ``event_demand_analytics`` dataset.

Run (repo root, ADC authed):

    python synth/generate.py --dry-run      # simulate + report, write nothing
    python synth/generate.py                # full generate + load
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from synth import heuristics as h  # noqa: E402

GENERATOR_VERSION = "1.0.0"
DEFAULT_PROJECT = "data-architecture-498123"
DEFAULT_SOURCE_DATASET = "event_demand_analytics"
DEFAULT_SYNTH_DATASET = "event_demand_synth"
DEFAULT_SEED = 683
ONSALE_HORIZON_DAYS = 45
SERIES_CHECKPOINTS = [45, 30, 21, 14, 7, 3, 1, 0]

FEATURES_SQL = """
WITH latest_price AS (
  SELECT event_id, price_min
  FROM `{src}.fact_event_demand`
  WHERE price_min IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY snapshot_date DESC) = 1
),
latest_subs AS (
  SELECT artist_id, official_subscribers
  FROM `{src}.fact_youtube`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY snapshot_date DESC) = 1
),
local_level AS (
  SELECT artist_id, dma_code, AVG(interest) AS local_interest_level
  FROM `{src}.fact_trends_daily`
  WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
  GROUP BY artist_id, dma_code
),
genre_median AS (
  SELECT e.primary_genre,
         APPROX_QUANTILES(f.price_min, 100)[OFFSET(50)] AS genre_median_price
  FROM `{src}.fact_event_demand` f
  JOIN `{src}.dim_event` e ON e.event_id = f.event_id
  WHERE f.price_min IS NOT NULL
  GROUP BY e.primary_genre
),
headliner AS (
  SELECT event_id, artist_id
  FROM `{src}.bridge_event_artist`
  WHERE is_headliner
  QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY billing_order) = 1
)
SELECT
  e.event_id, e.event_name, CAST(e.show_date AS STRING) AS show_date,
  e.primary_genre,
  v.venue_name, v.dma_code, v.capacity,
  a.artist_name,
  s.official_subscribers AS yt_subscribers,
  l.local_interest_level,
  p.price_min AS observed_price,
  g.genre_median_price
FROM `{src}.dim_event` e
JOIN `{src}.dim_venue` v ON e.venue_id = v.venue_id
LEFT JOIN headliner hd ON hd.event_id = e.event_id
LEFT JOIN `{src}.dim_artist` a ON a.artist_id = hd.artist_id
LEFT JOIN latest_subs s ON s.artist_id = hd.artist_id
LEFT JOIN local_level l ON l.artist_id = hd.artist_id AND l.dma_code = v.dma_code
LEFT JOIN latest_price p ON p.event_id = e.event_id
LEFT JOIN genre_median g ON g.primary_genre = e.primary_genre
WHERE e.show_date IS NOT NULL
"""

DEMAND_SCHEMA = [
    ("event_id", "STRING"), ("event_name", "STRING"), ("show_date", "DATE"),
    ("primary_genre", "STRING"), ("venue_name", "STRING"), ("dma_code", "STRING"),
    ("event_type", "STRING"), ("capacity", "INT64"), ("capacity_source", "STRING"),
    ("yt_subscribers", "INT64"), ("local_interest_level", "FLOAT64"),
    ("popularity", "FLOAT64"), ("demand_ratio", "FLOAT64"),
    ("sellout_probability", "FLOAT64"), ("sold_out", "BOOL"),
    ("sellout_days_before_show", "INT64"), ("sellout_date", "DATE"),
    ("face_price", "FLOAT64"), ("face_price_source", "STRING"),
    ("resale_multiplier", "FLOAT64"), ("resale_price_at_show", "FLOAT64"),
    ("synth_run_id", "STRING"), ("generator_version", "STRING"), ("seed", "INT64"),
]

SERIES_SCHEMA = [
    ("event_id", "STRING"), ("days_to_show", "INT64"),
    ("resale_multiplier", "FLOAT64"), ("resale_price", "FLOAT64"),
    ("synth_run_id", "STRING"),
]


def event_rng(base_seed: int, event_id: str) -> np.random.Generator:
    """Deterministic per-event stream: same (seed, event_id) → same draws,
    independent of row order and batching."""
    digest = hashlib.sha256(event_id.encode("utf-8")).digest()
    return np.random.default_rng([base_seed, int.from_bytes(digest[:8], "big")])


def _face_price(feature: dict) -> tuple[float, str]:
    observed = feature.get("observed_price")
    if observed is not None:
        return float(observed), "observed"
    median = feature.get("genre_median_price")
    if median is not None:
        return round(float(median), 2), "genre_median"
    return 40.0, "type_default"


def simulate_row(feature: dict, base_seed: int, run_id: str) -> tuple[dict, list[dict]]:
    """Pure per-event transform: feature row → (demand row, resale-series rows)."""
    rng = event_rng(base_seed, feature["event_id"])
    capacity_raw = feature.get("capacity")
    capacity_raw = int(capacity_raw) if capacity_raw is not None else None
    event_type = h.infer_event_type(feature.get("event_name"), capacity_raw)
    capacity = h.effective_capacity(capacity_raw, event_type)
    face, face_source = _face_price(feature)

    outcome = h.simulate_event(
        event_name=feature.get("event_name"),
        capacity=capacity_raw,
        local_interest=feature.get("local_interest_level"),
        yt_subscribers=feature.get("yt_subscribers"),
        onsale_horizon_days=ONSALE_HORIZON_DAYS,
        rng=rng,
    )

    show_date = date.fromisoformat(feature["show_date"])
    sellout_date = (
        show_date - timedelta(days=outcome.sellout_days_before_show)
        if outcome.sellout_days_before_show is not None else None
    )
    subs = feature.get("yt_subscribers")
    demand_row = {
        "event_id": feature["event_id"],
        "event_name": feature.get("event_name"),
        "show_date": feature["show_date"],
        "primary_genre": feature.get("primary_genre"),
        "venue_name": feature.get("venue_name"),
        "dma_code": feature.get("dma_code"),
        "event_type": event_type,
        "capacity": capacity,
        "capacity_source": "researched" if capacity_raw else "tier_estimate",
        "yt_subscribers": int(subs) if subs is not None else None,
        "local_interest_level": feature.get("local_interest_level"),
        "popularity": round(
            h.popularity_score(feature.get("local_interest_level"),
                               feature.get("yt_subscribers")), 4
        ),
        "demand_ratio": outcome.demand_ratio,
        "sellout_probability": outcome.sellout_probability,
        "sold_out": outcome.sold_out,
        "sellout_days_before_show": outcome.sellout_days_before_show,
        "sellout_date": sellout_date.isoformat() if sellout_date else None,
        "face_price": round(face, 2),
        "face_price_source": face_source,
        "resale_multiplier": outcome.resale_multiplier,
        "resale_price_at_show": round(face * outcome.resale_multiplier, 2),
        "synth_run_id": run_id,
        "generator_version": GENERATOR_VERSION,
        "seed": base_seed,
    }

    series = [
        {
            "event_id": feature["event_id"],
            "days_to_show": days,
            "resale_multiplier": h.resale_multiplier_at(
                outcome.resale_multiplier, days, ONSALE_HORIZON_DAYS
            ),
            "resale_price": round(
                face * h.resale_multiplier_at(
                    outcome.resale_multiplier, days, ONSALE_HORIZON_DAYS
                ), 2
            ),
            "synth_run_id": run_id,
        }
        for days in SERIES_CHECKPOINTS
        if days <= ONSALE_HORIZON_DAYS
    ]
    return demand_row, series


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def fetch_features(project: str, dataset: str, limit: int | None) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    sql = FEATURES_SQL.format(src=f"{project}.{dataset}")
    if limit:
        sql += f"\nLIMIT {int(limit)}"
    return [dict(row) for row in client.query(sql).result()]


def load_tables(project: str, synth_dataset: str,
                demand_rows: list[dict], series_rows: list[dict]) -> None:
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    client.query(
        f"CREATE SCHEMA IF NOT EXISTS `{project}.{synth_dataset}` "
        f"OPTIONS (location = 'us-west1')"
    ).result()

    for table, schema_def, rows in (
        ("synth_event_demand", DEMAND_SCHEMA, demand_rows),
        ("synth_resale_series", SERIES_SCHEMA, series_rows),
    ):
        job_config = bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField(name, type_) for name, type_ in schema_def],
            write_disposition="WRITE_TRUNCATE",
        )
        job = client.load_table_from_json(
            rows, f"{project}.{synth_dataset}.{table}", job_config=job_config
        )
        job.result()
        print(f"[synth] loaded {len(rows)} rows -> {project}.{synth_dataset}.{table}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_SOURCE_DATASET)
    parser.add_argument("--synth-dataset", default=DEFAULT_SYNTH_DATASET)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--limit", type=int, help="cap events (iteration aid)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.synth_dataset == args.dataset:
        raise SystemExit("refusing to write synth output into the source dataset")

    run_id = f"seed{args.seed}-v{GENERATOR_VERSION}"
    features = fetch_features(args.project, args.dataset, args.limit)
    demand_rows, series_rows = [], []
    for feature in features:
        demand, series = simulate_row(feature, args.seed, run_id)
        demand_rows.append(demand)
        series_rows.extend(series)

    sold = sum(r["sold_out"] for r in demand_rows)
    researched = sum(r["capacity_source"] == "researched" for r in demand_rows)
    above_face = sum(r["resale_multiplier"] > 1.0 for r in demand_rows)
    print(
        f"[synth] {len(demand_rows)} events simulated (run {run_id}): "
        f"{sold} sell out ({sold / max(len(demand_rows), 1):.1%}), "
        f"{above_face} resell above face, {researched} with researched capacity"
    )
    if args.dry_run:
        print("[synth] dry-run: nothing written")
        return
    load_tables(args.project, args.synth_dataset, demand_rows, series_rows)


if __name__ == "__main__":
    main()
