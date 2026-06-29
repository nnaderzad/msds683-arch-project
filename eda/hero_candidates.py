#!/usr/bin/env python3
"""Curate a vetted set of demo hero-show candidates for the UI dropdown.

Builds on `final_presentation/queries/hero_shows.sql` (full-coverage pool = a show with a
Ticketmaster price + a Google-Trends interest value + a YouTube subscriber count) and joins
each candidate to the **live anchor+drift forecast** so we only offer shows that demo
credibly. The forecast holds signals constant and projects price from "today" to the show
date; for the ~96%-flat majority that should stay near the show's real price, but long
horizons can let the pooled drift overshoot — so each candidate is classified:

  * ``flat``    |drift| < 5% of the real price       — forecast ~tracks the real price
  * ``rising``  +5%..+25%                            — mild upward demand pressure (fine to show)
  * ``falling`` -5%..-25%                            — mild downward
  * ``steep``   |drift| > 25%                        — overshoot risk; avoid as the live hero

`drift` = forecast at the show date − the show's latest observed real price (the anchor).
Bay-Area (DMA 807) and recognizable (YouTube subs) shows rank first among credible ones.

Outputs (consumed by the team / dropdown; no UI changes here):
  * final_presentation/hero-candidates.md   — ranked menu with the new-forecast columns
  * final_presentation/hero-candidates.csv  — event_id + label + fields for the dropdown

Deterministic + re-runnable (pure ranking; live read via the authed `bq` CLI). No LLM.

Run (repo root, bq authed):
    python eda/hero_candidates.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, bq_rows, utc_now_iso  # noqa: E402

OUT_DIR = REPO_ROOT / "final_presentation"
FLAT_PCT, STEEP_PCT = 0.05, 0.25


def load_candidates(project: str, dataset: str) -> list[dict]:
    """Full-coverage events joined to anchor (latest real price) + live forecast endpoints."""
    sql = f"""
        WITH per_event AS (
          SELECT event_id,
                 COUNT(DISTINCT snapshot_date)        AS n_snapshots,
                 COUNTIF(price_min IS NOT NULL)       AS n_priced,
                 COUNTIF(local_interest IS NOT NULL)  AS n_interest,
                 COUNTIF(yt_subscribers IS NOT NULL)  AS n_youtube,
                 MAX(price_min)                       AS price_min_seen,
                 MAX(price_max)                       AS price_max_seen,
                 MAX(yt_subscribers)                  AS yt_subs,
                 ANY_VALUE(dma_code)                  AS dma_code,
                 ANY_VALUE(artist_id)                 AS artist_id
          FROM `{project}.{dataset}.fact_event_demand`
          GROUP BY event_id
        ),
        anchor AS (
          SELECT event_id, price_min AS anchor_price FROM (
            SELECT event_id, price_min,
                   ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY snapshot_date DESC) AS rn
            FROM `{project}.{dataset}.fact_event_demand`
            WHERE price_min IS NOT NULL)
          WHERE rn = 1
        ),
        fcst AS (
          SELECT event_id,
                 MAX(days_to_show) AS horizon_days,
                 ARRAY_AGG(predicted_price ORDER BY days_to_show DESC LIMIT 1)[OFFSET(0)] AS fcst_today,
                 ARRAY_AGG(predicted_price ORDER BY days_to_show ASC  LIMIT 1)[OFFSET(0)] AS fcst_showdate
          FROM `{project}.{dataset}.forecast_event_price`
          GROUP BY event_id
        )
        SELECT pe.event_id, e.event_name, a.artist_name, v.venue_name, v.city, v.state_code,
               CAST(e.show_date AS STRING) AS show_date, e.primary_genre,
               pe.n_snapshots, pe.n_interest, pe.n_youtube,
               pe.price_min_seen, pe.price_max_seen, pe.yt_subs, pe.dma_code,
               an.anchor_price, fc.horizon_days, fc.fcst_today, fc.fcst_showdate
        FROM per_event pe
        LEFT JOIN `{project}.{dataset}.dim_event`  e  USING (event_id)
        LEFT JOIN `{project}.{dataset}.dim_artist` a  ON a.artist_id = pe.artist_id
        LEFT JOIN `{project}.{dataset}.dim_venue`  v  ON v.venue_id = e.venue_id
        LEFT JOIN anchor an USING (event_id)
        LEFT JOIN fcst   fc USING (event_id)
        WHERE pe.n_priced > 0 AND pe.n_interest > 0 AND pe.n_youtube > 0
    """
    return bq_rows(sql, project)


def _f(v: object) -> float | None:
    s = str(v).strip()
    if s == "" or s.lower() == "none":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def classify(rows: list[dict]) -> list[dict]:
    """Add drift, drift_pct, showcase class, and a dropdown label to each candidate."""
    out = []
    for r in rows:
        anchor = _f(r.get("anchor_price"))
        showdate = _f(r.get("fcst_showdate"))
        drift = (showdate - anchor) if (anchor is not None and showdate is not None) else None
        drift_pct = (drift / anchor) if (drift is not None and anchor) else None
        if drift_pct is None:
            showcase = "n/a"
        elif abs(drift_pct) < FLAT_PCT:
            showcase = "flat"
        elif drift_pct > STEEP_PCT or drift_pct < -STEEP_PCT:
            showcase = "steep"
        else:
            showcase = "rising" if drift_pct > 0 else "falling"
        is_bay = str(r.get("dma_code")) == "807"
        arrow = {"flat": "→", "rising": "↑", "falling": "↓", "steep": "⇈", "n/a": "?"}[showcase]
        artist = (r.get("artist_name") or r.get("event_name") or "?").strip()
        label = (f"{artist} — {r.get('venue_name') or '?'}, {r.get('city') or '?'} "
                 f"(${_round(anchor)} fcst ${_round(showdate)}{arrow})")
        out.append({**r, "anchor_price": anchor, "fcst_showdate": showdate,
                    "fcst_today": _f(r.get("fcst_today")), "yt_subs": _f(r.get("yt_subs")),
                    "n_interest": int(_f(r.get("n_interest")) or 0),
                    "n_youtube": int(_f(r.get("n_youtube")) or 0),
                    "n_snapshots": int(_f(r.get("n_snapshots")) or 0),
                    "drift": drift, "drift_pct": drift_pct, "showcase": showcase,
                    "is_bay_area": is_bay, "label": label})
    return out


def _round(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}"


# Credible-first ordering: Bay-Area, then forecast that tracks/mildly-moves (not steep),
# then richer coverage, then recognizability (YouTube subs).
_SHOWCASE_RANK = {"flat": 0, "rising": 1, "falling": 2, "steep": 3, "n/a": 4}


def rank(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: (
        not r["is_bay_area"],
        _SHOWCASE_RANK[r["showcase"]],
        -(r["n_interest"] + r["n_youtube"]),
        -(r["yt_subs"] or 0),
    ))


_CSV_FIELDS = ["event_id", "label", "artist_name", "event_name", "venue_name", "city",
               "state_code", "show_date", "is_bay_area", "n_snapshots", "n_interest",
               "n_youtube", "anchor_price", "fcst_today", "fcst_showdate", "drift_pct",
               "showcase", "yt_subs"]


def write_outputs(records: list[dict], out_dir: Path, as_of: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "hero-candidates.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            row = dict(r)
            row["drift_pct"] = "" if r["drift_pct"] is None else round(r["drift_pct"], 3)
            w.writerow(row)

    bay = [r for r in records if r["is_bay_area"]]
    credible = [r for r in records if r["showcase"] in ("flat", "rising", "falling")]
    lines = [
        "# Hero-show candidates (vetted against the live anchor forecast)",
        "",
        f"_Generated by `eda/hero_candidates.py`, as of {as_of}. Re-run to refresh._",
        "",
        "Full-coverage shows (price + interest + youtube) scored against the live "
        "`forecast_event_price`. `fcst` = forecast at the show date; `drift%` = how far that "
        "sits from the show's real latest price (the anchor). Prefer **flat/rising** for the "
        "live hero; **steep** shows let the pooled drift overshoot a long horizon — avoid.",
        "",
        f"- **{len(records)}** full-coverage candidates · **{len(bay)}** Bay-Area · "
        f"**{len(credible)}** credible (non-steep).",
        "",
        "| rank | artist / event | venue, city | bay? | snaps | int/yt | real $ | fcst $ | drift% | class |",
        "|--:|---|---|:--:|--:|--:|--:|--:|--:|---|",
    ]
    for i, r in enumerate(records, 1):
        name = r.get("artist_name") or r.get("event_name") or "?"
        drift_txt = "—" if r["drift_pct"] is None else f"{r['drift_pct'] * 100:+.0f}%"
        bay_txt = "✅" if r["is_bay_area"] else ""
        lines.append(
            f"| {i} | {name} | {r.get('venue_name') or '?'}, {r.get('city') or '?'} | "
            f"{bay_txt} | {r['n_snapshots']} | {r['n_interest']}/{r['n_youtube']} | "
            f"${_round(r['anchor_price'])} | ${_round(r['fcst_showdate'])} | "
            f"{drift_txt} | {r['showcase']} |")
    lines += ["", "_Event IDs + dropdown labels are in `hero-candidates.csv`._", ""]
    md_path = out_dir / "hero-candidates.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", default=str(OUT_DIR))
    args = parser.parse_args()

    rows = load_candidates(args.project, args.dataset)
    records = rank(classify(rows))
    md_path, csv_path = write_outputs(records, Path(args.output_dir), utc_now_iso())
    bay = sum(1 for r in records if r["is_bay_area"])
    steep = sum(1 for r in records if r["showcase"] == "steep")
    print(f"[hero_candidates] {len(records)} full-coverage candidates "
          f"({bay} Bay-Area, {steep} steep/overshoot); wrote {md_path} + {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
