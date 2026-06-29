#!/usr/bin/env python3
"""Model design step 1 — forecast-vs-actual bias, OLD (live) vs NEW (anchor) model.

Quantifies the #1 demo risk — the forecast contradicting the real price — and proves the
anchor+drift change fixes it, **without writing to the live table** (sign-off gate). For
each event it compares the forecast at the nearest day-to-show against the actual latest
observed `price_min`, by price tier, plus the hero show and the premium-tail reachability.

Two sources, one apples-to-apples report:
  * ``live``    — the current `forecast_event_price` gold table (the OLD pooled-mean model).
  * ``rebuild`` — the NEW anchor+drift model assembled in-process from the same gold
    (`export_predictions_table.assemble_forecast`); nothing is written.

Actuals come from the same `fact_event_demand` read, so OLD and NEW are scored identically.
Deterministic + re-runnable (fixed seed in the model; pure bias math, offline-testable).
No LLM at runtime.

Run (repo root, BigQuery ADC authed):
    python eda/diagnose_forecast_bias.py                 # both sources
    python eda/diagnose_forecast_bias.py --sources live  # just the current table
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "model"))
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "gold"))
import export_predictions_table as export  # noqa: E402  (inserts model/ on path, imports D1/D2)
import features  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
HERO_EVENT_ID = "rZ7HnEZ1Af00jd"  # Everclear @ The Independent SF (final_presentation/hero-shows.md)
PREMIUM = 90.0                     # the old model topped out ~$92; the tail it can't reach
# Price tiers by ACTUAL price: (label, lo_inclusive, hi_exclusive)
TIERS = [("<$30", 0.0, 30.0), ("$30-75", 30.0, 75.0),
         ("$75-150", 75.0, 150.0), ("$150+", 150.0, math.inf)]


# ---------------------------------------------------------------------------
# Pure bias math (offline-testable; no network)
# ---------------------------------------------------------------------------

def actuals_from_gold(gold: pd.DataFrame) -> pd.DataFrame:
    """Latest observed real price per event: (event_id, actual_price)."""
    priced = gold[gold["price_min"].notna()].copy()
    priced["snapshot_date"] = pd.to_datetime(priced["snapshot_date"], errors="coerce")
    idx = priced.groupby("event_id")["snapshot_date"].idxmax()
    return (priced.loc[idx, ["event_id", "price_min"]]
            .rename(columns={"price_min": "actual_price"}).reset_index(drop=True))


def nearest_forecast(forecast: pd.DataFrame) -> pd.DataFrame:
    """Forecast at the nearest day-to-show per event: (event_id, forecast_price)."""
    f = forecast.copy()
    f["days_to_show"] = pd.to_numeric(f["days_to_show"], errors="coerce")
    f["predicted_price"] = pd.to_numeric(f["predicted_price"], errors="coerce")
    idx = f.groupby("event_id")["days_to_show"].idxmin()
    return (f.loc[idx, ["event_id", "predicted_price"]]
            .rename(columns={"predicted_price": "forecast_price"}).reset_index(drop=True))


def join_bias(actuals: pd.DataFrame, forecast: pd.DataFrame) -> pd.DataFrame:
    """Inner-join actual vs forecast; add signed error (forecast − actual) and abs error."""
    j = actuals.merge(nearest_forecast(forecast), on="event_id", how="inner")
    j["error"] = j["forecast_price"] - j["actual_price"]
    j["abs_error"] = j["error"].abs()
    return j


def _tier_of(actual: float) -> str:
    for label, lo, hi in TIERS:
        if lo <= actual < hi:
            return label
    return TIERS[-1][0]


def summarize_bias(joined: pd.DataFrame) -> dict:
    """Overall + per-tier bias, range coverage, and premium-tail accuracy."""
    n = len(joined)
    if n == 0:
        return {"n_events": 0}
    tiers = []
    for label, _lo, _hi in TIERS:
        grp = joined[joined["actual_price"].map(_tier_of) == label]
        if len(grp) == 0:
            continue
        tiers.append({
            "tier": label,
            "n": len(grp),
            "mean_signed_err": round(grp["error"].mean(), 2),
            "mae": round(grp["abs_error"].mean(), 2),
            "median_actual": round(grp["actual_price"].median(), 2),
            "median_forecast": round(grp["forecast_price"].median(), 2),
        })
    premium = joined[joined["actual_price"] > PREMIUM]
    return {
        "n_events": n,
        "mae": round(joined["abs_error"].mean(), 2),
        "median_signed_err": round(joined["error"].median(), 2),
        "mean_signed_err": round(joined["error"].mean(), 2),
        "pct_underpredict": round(100 * (joined["error"] < 0).mean(), 1),
        "forecast_min": round(joined["forecast_price"].min(), 2),
        "forecast_max": round(joined["forecast_price"].max(), 2),
        "actual_min": round(joined["actual_price"].min(), 2),
        "actual_max": round(joined["actual_price"].max(), 2),
        "n_premium": len(premium),
        "premium_mae": round(premium["abs_error"].mean(), 2) if len(premium) else None,
        "premium_mean_signed_err": round(premium["error"].mean(), 2) if len(premium) else None,
        "tiers": tiers,
    }


def hero_row(joined: pd.DataFrame, event_id: str) -> dict | None:
    h = joined[joined["event_id"] == event_id]
    if h.empty:
        return None
    r = h.iloc[0]
    return {"actual": round(float(r["actual_price"]), 2),
            "forecast": round(float(r["forecast_price"]), 2),
            "error": round(float(r["error"]), 2)}


# ---------------------------------------------------------------------------
# BigQuery I/O (thin; the math above stays pure)
# ---------------------------------------------------------------------------

def read_live_forecast(project: str | None, dataset: str | None) -> pd.DataFrame:
    """Read the current `forecast_event_price` gold table (the OLD model)."""
    from google.cloud import bigquery

    import os
    project = project or os.environ.get("DBT_GCP_PROJECT", "data-architecture-498123")
    dataset = dataset or os.environ.get("DBT_BQ_DATASET", "event_demand_analytics")
    client = bigquery.Client(project=project)
    sql = (f"SELECT event_id, days_to_show, predicted_price "
           f"FROM `{project}.{dataset}.forecast_event_price`")
    return features._to_numpy_backed(client.query(sql).to_dataframe())


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _section(name: str, s: dict, hero: dict | None) -> list[str]:
    if s.get("n_events", 0) == 0:
        return [f"## {name}", "", "_No scored events._", ""]
    lines = [
        f"## {name}",
        "",
        f"- Scored **{s['n_events']:,}** events. **MAE ${s['mae']}**, median signed error "
        f"${s['median_signed_err']} (mean ${s['mean_signed_err']}); "
        f"**{s['pct_underpredict']}%** underpredict.",
        f"- Forecast range **${s['forecast_min']}–${s['forecast_max']}** vs actual "
        f"**${s['actual_min']}–${s['actual_max']}**.",
        f"- Premium tail (> ${PREMIUM:.0f}): **{s['n_premium']:,}** shows, MAE "
        f"**${s['premium_mae']}**, mean signed error ${s['premium_mean_signed_err']}.",
    ]
    if hero:
        lines.append(f"- **Hero (Everclear `{HERO_EVENT_ID}`):** actual **${hero['actual']}** "
                     f"vs forecast **${hero['forecast']}** (error ${hero['error']}).")
    lines += ["", "| tier (by actual) | n | median actual | median forecast | mean signed err | MAE |",
              "|---|--:|--:|--:|--:|--:|"]
    for t in s["tiers"]:
        lines.append(f"| {t['tier']} | {t['n']:,} | ${t['median_actual']} | "
                     f"${t['median_forecast']} | ${t['mean_signed_err']} | ${t['mae']} |")
    lines.append("")
    return lines


def write_report(out_dir: Path, results: dict[str, dict], as_of: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Forecast-vs-actual bias: OLD (live) vs NEW (anchor+drift)",
        "",
        f"_Generated by `eda/diagnose_forecast_bias.py`, as of {as_of}. Read-only "
        "(NEW model assembled in-process; nothing written to the live table). Re-run to refresh._",
        "",
        "Each event's forecast at the nearest day-to-show vs its actual latest observed "
        "`price_min`. The OLD pooled-mean model range-compresses (can't reach the premium "
        "tail); the NEW model anchors each show at its real price, so it starts on-price and "
        "spans the full range.",
        "",
    ]
    for name in results:
        lines += _section(name, results[name]["summary"], results[name]["hero"])
    path = out_dir / "forecast_bias.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=None)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--sources", nargs="+", default=["live", "rebuild"],
                        choices=["live", "rebuild"])
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    from _common import utc_now_iso

    gold, dim_venue, dim_event = features.read_gold_and_dims(args.project, args.dataset)
    actuals = actuals_from_gold(gold)
    results: dict[str, dict] = {}

    if "live" in args.sources:
        live = read_live_forecast(args.project, args.dataset)
        joined = join_bias(actuals, live)
        results["OLD — live `forecast_event_price`"] = {
            "summary": summarize_bias(joined), "hero": hero_row(joined, HERO_EVENT_ID)}

    if "rebuild" in args.sources:
        new_forecast = export.assemble_forecast(gold, dim_venue, dim_event)
        joined = join_bias(actuals, new_forecast)
        results["NEW — anchor+drift (in-process, unwritten)"] = {
            "summary": summarize_bias(joined), "hero": hero_row(joined, HERO_EVENT_ID)}

    report = write_report(Path(args.output_dir), results, utc_now_iso())
    for name, r in results.items():
        s, h = r["summary"], r["hero"]
        hero_txt = f"; hero ${h['forecast']} vs ${h['actual']}" if h else ""
        print(f"[forecast_bias] {name}: MAE ${s['mae']}, range "
              f"${s['forecast_min']}-${s['forecast_max']}, {s['n_premium']} premium "
              f"(MAE ${s['premium_mae']}){hero_txt}.")
    print(f"[forecast_bias] wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
