#!/usr/bin/env python3
"""Model design step 0 — how much does ticket price actually MOVE within a show?

The price forecaster's architecture hinges on one empirical fact. If `price_min`
is largely **flat** across each show's snapshot window, there is almost no
within-show dynamic to model: the forecasting value is in pinning each show at its
own real price *level* (a per-show anchor), not in projecting drift — and a pooled
model that ignores the level (the current one) range-compresses to the global mean.
If prices genuinely move, a drift/lag model earns its keep. This script measures it.

Per priced event it computes, over that event's observed price series:
  * n_priced          = snapshots carrying a price (real history length)
  * n_distinct_price  = distinct price levels seen (==1 -> perfectly flat)
  * price_first/last  = earliest / latest observed price (the anchor candidates)
  * price_lo/hi       = min / max over the series (movement envelope)
  * n_changes         = day-over-day price changes (LAG); total_abs_variation = sum |Δ|

It then summarizes: % of shows that are flat, the distinct-price and history-length
distributions, movement magnitude among movers, and the **cross-show price LEVEL
spread** (what range the anchor must span) incl. counts of premium (> $90 / > $150)
shows the current model structurally can't reach.

Honesty note — it scans BOTH tables and reports the difference:
  * `tm_observations`   = honest observed-only price history (one row per day actually
    captured). The truthful source for "do prices move", but currently a 9-day window.
  * `fact_event_demand` = the gold table the live model trains on, which is **forward-
    filled** (last-known price carried onto non-observed days), so flatness and history
    length are inflated; real day-over-day *changes* are still preserved. Reading both
    shows how much the forward-fill flatters the picture.

Deterministic + re-runnable: per-event aggregation runs in BigQuery via the authed
`bq` CLI; the summarizer is a pure function, unit-testable offline. No LLM at runtime.

Run (repo root, bq authed for the project):
    python eda/diagnose_price_movement.py
    python eda/diagnose_price_movement.py --tables tm_observations
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eda"))
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, bq_rows, utc_now_iso  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DEFAULT_TABLES = ["tm_observations", "fact_event_demand"]
PREMIUM_THRESHOLDS = (90.0, 150.0)  # current forecast tops out ~$92; flag what it can't reach


# ---------------------------------------------------------------------------
# BigQuery I/O (thin; the summarizer below stays pure + offline-testable)
# ---------------------------------------------------------------------------

def load_event_movement(project: str, dataset: str, table: str) -> list[dict[str, str]]:
    """One row per priced event with its price-series movement aggregates.

    Restricted to rows carrying a price (`price_min IS NOT NULL`) — the universe a
    price forecast operates on. Per-event aggregation happens in BigQuery so only
    ~one row per event crosses the wire.
    """

    sql = f"""
        WITH priced AS (
          SELECT event_id, snapshot_date, price_min
          FROM `{project}.{dataset}.{table}`
          WHERE price_min IS NOT NULL
        ),
        seq AS (
          SELECT event_id, snapshot_date, price_min,
                 LAG(price_min) OVER (
                     PARTITION BY event_id ORDER BY snapshot_date) AS prev_price
          FROM priced
        )
        SELECT
          event_id,
          COUNT(*)                                              AS n_priced,
          COUNT(DISTINCT price_min)                             AS n_distinct_price,
          MIN(price_min)                                        AS price_lo,
          MAX(price_min)                                        AS price_hi,
          ARRAY_AGG(price_min ORDER BY snapshot_date ASC  LIMIT 1)[OFFSET(0)] AS price_first,
          ARRAY_AGG(price_min ORDER BY snapshot_date DESC LIMIT 1)[OFFSET(0)] AS price_last,
          COUNTIF(prev_price IS NOT NULL AND price_min != prev_price)         AS n_changes,
          SUM(ABS(price_min - prev_price))                      AS total_abs_variation
        FROM seq
        GROUP BY event_id
    """
    return bq_rows(sql, project)


# ---------------------------------------------------------------------------
# Pure summarizer (unit-testable offline; no network)
# ---------------------------------------------------------------------------

def _f(v: object) -> float | None:
    s = str(v).strip()
    if s == "" or s.lower() == "none":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_event_rows(rows: list[dict]) -> list[dict]:
    """Coerce bq CSV strings to typed per-event movement records."""

    out = []
    for r in rows:
        n_priced = int(_f(r.get("n_priced")) or 0)
        if n_priced <= 0:
            continue
        price_first = _f(r.get("price_first"))
        price_lo = _f(r.get("price_lo"))
        price_hi = _f(r.get("price_hi"))
        rel_range = None
        if price_first and price_first > 0 and price_lo is not None and price_hi is not None:
            rel_range = (price_hi - price_lo) / price_first
        out.append({
            "event_id": r["event_id"],
            "n_priced": n_priced,
            "n_distinct_price": int(_f(r.get("n_distinct_price")) or 0),
            "price_first": price_first,
            "price_last": _f(r.get("price_last")),
            "price_lo": price_lo,
            "price_hi": price_hi,
            "abs_range": (price_hi - price_lo) if (price_lo is not None and price_hi is not None) else None,
            "rel_range": rel_range,
            "n_changes": int(_f(r.get("n_changes")) or 0),
            "total_abs_variation": _f(r.get("total_abs_variation")) or 0.0,
            "is_flat": int(_f(r.get("n_distinct_price")) or 0) <= 1,
        })
    return out


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def _bucket(value: int, edges: list[int]) -> str:
    """Label `value` into half-open buckets defined by ascending `edges`."""
    prev = edges[0]
    if value < prev:
        return f"<{prev}"
    for e in edges[1:]:
        if value < e:
            return f"{prev}-{e - 1}"
        prev = e
    return f"{prev}+"


def summarize_movement(events: list[dict]) -> dict:
    """Aggregate per-event movement into the architecture-deciding headline stats."""

    n = len(events)
    if n == 0:
        return {"n_events": 0}

    flat = [e for e in events if e["is_flat"]]
    movers = [e for e in events if not e["is_flat"]]
    single_obs = [e for e in events if e["n_priced"] == 1]

    # distinct-price distribution
    distinct_dist: dict[str, int] = {}
    for e in events:
        distinct_dist[_bucket(e["n_distinct_price"], [1, 2, 3, 4])] = (
            distinct_dist.get(_bucket(e["n_distinct_price"], [1, 2, 3, 4]), 0) + 1)

    # history-length distribution
    hist_dist: dict[str, int] = {}
    for e in events:
        hist_dist[_bucket(e["n_priced"], [1, 2, 4, 8])] = (
            hist_dist.get(_bucket(e["n_priced"], [1, 2, 4, 8]), 0) + 1)

    rel_moves = sorted(e["rel_range"] for e in movers if e["rel_range"] is not None)
    abs_moves = sorted(e["abs_range"] for e in movers if e["abs_range"] is not None)
    levels = sorted(e["price_last"] for e in events if e["price_last"] is not None)

    def lvl(p):
        v = _percentile(levels, p)
        return round(v, 2) if v is not None else None

    def rel(p):
        v = _percentile(rel_moves, p)
        return round(v, 4) if v is not None else None

    def absp(p):
        v = _percentile(abs_moves, p)
        return round(v, 2) if v is not None else None

    return {
        "n_events": n,
        "n_flat": len(flat),
        "pct_flat": round(100 * len(flat) / n, 1),
        "n_movers": len(movers),
        "pct_movers": round(100 * len(movers) / n, 1),
        "n_single_obs": len(single_obs),
        "pct_single_obs": round(100 * len(single_obs) / n, 1),
        "distinct_price_dist": distinct_dist,
        "history_len_dist": hist_dist,
        # movement magnitude among movers
        "mover_rel_range_p50": rel(50),
        "mover_rel_range_p90": rel(90),
        "mover_abs_range_p50": absp(50),
        "mover_abs_range_p90": absp(90),
        # cross-show price LEVEL spread (what the anchor must span)
        "level_p10": lvl(10),
        "level_p50": lvl(50),
        "level_p90": lvl(90),
        "level_p99": lvl(99),
        "level_max": round(max(levels), 2) if levels else None,
        "n_premium_90": sum(1 for v in levels if v > PREMIUM_THRESHOLDS[0]),
        "n_premium_150": sum(1 for v in levels if v > PREMIUM_THRESHOLDS[1]),
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_PER_EVENT_FIELDS = [
    "event_id", "n_priced", "n_distinct_price", "price_first", "price_last",
    "price_lo", "price_hi", "abs_range", "rel_range", "n_changes",
    "total_abs_variation", "is_flat",
]


def write_per_event_csv(out_dir: Path, table: str, events: list[dict]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"price_movement_{table}.csv"
    ordered = sorted(events, key=lambda e: (e["is_flat"], -(e["abs_range"] or 0), e["event_id"]))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_PER_EVENT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    return path


def _table_section(table: str, s: dict) -> list[str]:
    if s.get("n_events", 0) == 0:
        return [f"## `{table}`", "", "_No priced events found._", ""]
    dd = s["distinct_price_dist"]
    hd = s["history_len_dist"]
    return [
        f"## `{table}`",
        "",
        f"- **{s['n_events']:,}** priced events.",
        f"- **{s['pct_flat']}%** are perfectly **flat** ({s['n_flat']:,} events, one price level "
        f"all window); **{s['pct_movers']}%** move ({s['n_movers']:,}).",
        f"- **{s['pct_single_obs']}%** have a **single** priced observation "
        f"({s['n_single_obs']:,}) -> anchor falls back to that lone price.",
        "- Distinct price levels per show: " + ", ".join(
            f"`{k}`={v:,}" for k, v in sorted(dd.items())) + ".",
        "- Real history length (priced snapshots): " + ", ".join(
            f"`{k}`={v:,}" for k, v in sorted(hd.items())) + ".",
        f"- Among movers, price range = `{s['mover_abs_range_p50']}` median / "
        f"`{s['mover_abs_range_p90']}` p90 absolute "
        f"(`{s['mover_rel_range_p50']}` / `{s['mover_rel_range_p90']}` relative to first price).",
        f"- **Cross-show price level** (last observed): p10 `{s['level_p10']}` / "
        f"p50 `{s['level_p50']}` / p90 `{s['level_p90']}` / p99 `{s['level_p99']}` / "
        f"max `{s['level_max']}`.",
        f"- Premium shows the current $0-92 model can't reach: **{s['n_premium_90']:,}** > $90, "
        f"**{s['n_premium_150']:,}** > $150.",
        "",
    ]


def write_report(out_dir: Path, summaries: dict[str, dict], as_of: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Ticket-price movement diagnosis (model design step 0)",
        "",
        f"_Generated by `eda/diagnose_price_movement.py`, as of {as_of}. Re-run to refresh._",
        "",
        "Decides the forecaster architecture: if prices are **flat**, the value is anchoring "
        "each show at its real price *level* (a per-show anchor), not modeling drift. "
        "`tm_observations` is the honest observed-only history (truthful but ~9 days); "
        "`fact_event_demand` is the forward-filled gold the live model trains on (flatness "
        "and history length inflated; real day-over-day changes preserved).",
        "",
    ]
    for table in summaries:
        lines += _table_section(table, summaries[table])
    path = out_dir / "price_movement.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--tables", nargs="+", default=DEFAULT_TABLES)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    as_of = utc_now_iso()
    out_dir = Path(args.output_dir)
    summaries: dict[str, dict] = {}
    for table in args.tables:
        rows = load_event_movement(args.project, args.dataset, table)
        events = parse_event_rows(rows)
        summaries[table] = summarize_movement(events)
        write_per_event_csv(out_dir, table, events)
        s = summaries[table]
        if s.get("n_events", 0):
            print(f"[price_movement] {table}: {s['n_events']:,} priced events; "
                  f"{s['pct_flat']}% flat, {s['pct_single_obs']}% single-obs; "
                  f"level p50 {s['level_p50']} / p90 {s['level_p90']} / max {s['level_max']}; "
                  f"{s['n_premium_90']:,} shows > $90.")
        else:
            print(f"[price_movement] {table}: no priced events found.")

    report = write_report(out_dir, summaries, as_of)
    print(f"[price_movement] wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
