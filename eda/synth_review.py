#!/usr/bin/env python3
"""QC review of the synthetic demand layer (`event_demand_synth`).

Deterministic, committed sanity report for the generator's output: does the
synthetic world behave like the market stories it encodes, and is every row
properly labeled? Compares synth aggregates against the real warehouse where a
real anchor exists (face prices), and probes the two anecdote regimes directly
(popular act / small room vs soft big room).

Run (repo root, ADC authed) after `python synth/generate.py`:

    python eda/synth_review.py

Writes ``eda/output/synth_review.md``. Deterministic given the tables: the same
synth_run_id reproduces the identical report (timestamp aside).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
from _common import DEFAULT_PROJECT, bq_rows, utc_now_iso  # noqa: E402

OUT_MD = REPO_ROOT / "eda" / "output" / "synth_review.md"
SYNTH_DATASET = "event_demand_synth"
REAL_DATASET = "event_demand_analytics"

BAND_SQL = """
SELECT
  CASE
    WHEN demand_ratio < 0.5 THEN 'a: <0.5 (soft)'
    WHEN demand_ratio < 1.0 THEN 'b: 0.5-1.0'
    WHEN demand_ratio < 2.0 THEN 'c: 1.0-2.0 (hot)'
    ELSE 'd: >=2.0 (oversubscribed)'
  END AS demand_band,
  COUNT(*) AS events,
  ROUND(AVG(CAST(sold_out AS INT64)) * 100, 1) AS pct_sold_out,
  ROUND(APPROX_QUANTILES(resale_multiplier, 100)[OFFSET(50)], 2) AS median_resale_mult
FROM `{p}.{s}.synth_event_demand`
GROUP BY demand_band ORDER BY demand_band
"""

TYPE_SQL = """
SELECT event_type, COUNT(*) AS events,
  ROUND(AVG(CAST(sold_out AS INT64)) * 100, 1) AS pct_sold_out,
  ROUND(APPROX_QUANTILES(demand_ratio, 100)[OFFSET(50)], 3) AS median_ratio
FROM `{p}.{s}.synth_event_demand`
GROUP BY event_type ORDER BY events DESC
"""

PROVENANCE_SQL = """
SELECT synth_run_id, generator_version, seed,
  COUNT(*) AS events,
  COUNTIF(capacity_source = 'researched') AS researched_capacity,
  COUNTIF(face_price_source = 'observed') AS observed_face,
  COUNTIF(face_price_source = 'genre_median') AS genre_median_face
FROM `{p}.{s}.synth_event_demand`
GROUP BY 1, 2, 3
"""

ANECDOTE_SQL = """
SELECT
  CASE
    WHEN capacity <= 800 AND demand_ratio >= 1.5 THEN 'in-demand act, small room (MGMT regime)'
    WHEN capacity >= 2500 AND demand_ratio < 0.7 THEN 'soft big room (fire-sale regime)'
  END AS regime,
  COUNT(*) AS events,
  ROUND(AVG(CAST(sold_out AS INT64)) * 100, 1) AS pct_sold_out,
  ROUND(APPROX_QUANTILES(resale_multiplier, 100)[OFFSET(50)], 2) AS median_resale_mult
FROM `{p}.{s}.synth_event_demand`
WHERE (capacity <= 800 AND demand_ratio >= 1.5) OR (capacity >= 2500 AND demand_ratio < 0.7)
GROUP BY regime ORDER BY regime
"""

FACE_VS_REAL_SQL = """
WITH synth AS (
  SELECT primary_genre,
    ROUND(APPROX_QUANTILES(face_price, 100)[OFFSET(50)], 2) AS synth_median_face,
    COUNT(*) AS synth_events
  FROM `{p}.{s}.synth_event_demand` GROUP BY primary_genre
),
real AS (
  SELECT e.primary_genre,
    ROUND(APPROX_QUANTILES(f.price_min, 100)[OFFSET(50)], 2) AS real_median_price
  FROM `{p}.{r}.fact_event_demand` f
  JOIN `{p}.{r}.dim_event` e ON e.event_id = f.event_id
  WHERE f.price_min IS NOT NULL GROUP BY e.primary_genre
)
SELECT s.primary_genre, s.synth_events, s.synth_median_face, r.real_median_price
FROM synth s LEFT JOIN real r ON r.primary_genre = s.primary_genre
ORDER BY s.synth_events DESC LIMIT 12
"""

SERIES_SQL = """
SELECT days_to_show,
  ROUND(APPROX_QUANTILES(resale_multiplier, 100)[OFFSET(50)], 3) AS median_mult
FROM `{p}.{s}.synth_resale_series`
GROUP BY days_to_show ORDER BY days_to_show DESC
"""


def md_table(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["_no rows_"]
    headers = list(rows[0].keys())
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(r.get(h, "")) for h in headers) + " |" for r in rows]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    args = parser.parse_args()

    fmt = {"p": args.project, "s": SYNTH_DATASET, "r": REAL_DATASET}
    sections = [
        ("Provenance & labeling", PROVENANCE_SQL),
        ("Sellout + resale by demand band (must be monotone)", BAND_SQL),
        ("By event type", TYPE_SQL),
        ("Anecdote regimes (the heuristics' ground truth)", ANECDOTE_SQL),
        ("Synth face price vs real observed median, by genre", FACE_VS_REAL_SQL),
        ("Resale trajectory (median multiplier by days-to-show)", SERIES_SQL),
    ]

    lines = [
        "# Synthetic layer QC review",
        "",
        f"Generated {utc_now_iso()} by `eda/synth_review.py`. Source: "
        f"`{SYNTH_DATASET}.synth_event_demand` / `synth_resale_series` "
        "(hybrid design: real event spine, synthetic sellout/resale infill — "
        "see synth/heuristics.py for the encoded market stories).",
    ]
    for title, sql in sections:
        rows = bq_rows(sql.format(**fmt), args.project)
        lines += ["", f"## {title}", ""] + md_table(rows)

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[synth_review] wrote {OUT_MD}")


if __name__ == "__main__":
    main()
