#!/usr/bin/env python3
"""Profile the *historical* Ticketmaster price coverage from the daily snapshots.

The bottleneck for any price forecast is **historical price depth**, and that does
NOT live in the current-state `tm_events` table (one row per event). It lives in the
per-day `tm_events` parquet dumps the pipeline writes to the processed bucket:

    gs://<project>-processed/ticketmaster/dt=YYYY-MM-DD/tm_events_*.parquet

Stacking those daily snapshots IS the event x snapshot-day price panel. This script
reads them as one ephemeral BigQuery external table (no persistent object; the `dt=`
of each row is recovered from the `_FILE_NAME` pseudo-column), then reports, per show:

  - how many distinct days it appears (`snapshot_days`) and how many of those carry a
    price (`days_with_price`) -> `price_completeness` = priced-days / present-days;
  - `coverage_span` = present-days / total snapshot days in the window;
  - whether it is **complete** (present ~all of the window AND priced ~every present
    day) -- a row that exists daily but is missing price is a worthless entry.

It rolls completeness up by genre / state / DMA / headliner, breaks down `price_type`
(Ticketmaster exposes FACE VALUE, not resale -- so any resale-typed rows are visible
in the data, not assumed), and plots the price trajectory for a few of the most
complete shows.

Deterministic + re-runnable: committed SQL + pure functions, no LLM at runtime. Every
output is stamped with the exact `dt=` partitions consumed + an as-of timestamp, so a
teammate can re-run in two weeks against more data and diff how coverage grew.

Run (repo root, `bq`/`gsutil` authed for the project):
    python eda/tm_price_eda.py
    python eda/tm_price_eda.py --start-date 2026-06-11 --end-date 2026-06-25 --plot-top-n 6

Writes CSVs + tm_price_eda.md + plots/ under eda/output/.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "google_trends_api"))
from geo_lookup import GeoLookup  # noqa: E402
from _common import DEFAULT_PROJECT, bq_rows, utc_now_iso  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
PLOTS_DIR = OUTPUT_DIR / "plots"
EDM_GENRES = ("Dance/Electronic", "Electronic")
SF_DMA = "807"  # San Francisco-Oakland-San Jose -- the Bay Area metro

# "Complete" = present on ~all of the window AND priced on ~every present day.
COMPLETE_SPAN = 0.9
COMPLETE_PRICE = 0.9

_DT_RE = re.compile(r"dt=([0-9]{4}-[0-9]{2}-[0-9]{2})")


# ---------------------------------------------------------------------------
# GCS / BigQuery I/O (kept thin so the pure functions below stay unit-testable)
# ---------------------------------------------------------------------------

def list_snapshot_uris(project: str, start: str | None, end: str | None) -> list[str]:
    """List the daily processed parquet snapshots, optionally bounded by dt=."""

    pattern = f"gs://{project}-processed/ticketmaster/dt=*/*.parquet"
    proc = subprocess.run(["gsutil", "ls", pattern],
                          capture_output=True, text=True, check=True)
    uris = [u.strip() for u in proc.stdout.splitlines() if u.strip().endswith(".parquet")]
    kept = []
    for uri in uris:
        dt = dt_from_uri(uri)
        if dt is None:
            continue
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        kept.append(uri)
    return sorted(kept)


def dt_from_uri(uri: str) -> str | None:
    m = _DT_RE.search(uri)
    return m.group(1) if m else None


def _external_def(uris: list[str]) -> str:
    return "tmhist::PARQUET=" + ",".join(uris)


def load_event_panel(project: str, uris: list[str]) -> list[dict[str, str]]:
    """One row per event, aggregated across the daily snapshots (raw strings)."""

    sql = """
        SELECT
          event_id,
          ANY_VALUE(event_name) AS event_name,
          ANY_VALUE(genre) AS genre,
          ANY_VALUE(venue_name) AS venue_name,
          ANY_VALUE(venue_city) AS venue_city,
          ANY_VALUE(venue_state_code) AS venue_state_code,
          ANY_VALUE(venue_postal_code) AS venue_postal_code,
          ANY_VALUE(attraction_names) AS attraction_names,
          CAST(ANY_VALUE(local_date) AS STRING) AS show_date,
          COUNT(DISTINCT snapshot_date) AS snapshot_days,
          COUNT(DISTINCT IF(price_min IS NOT NULL, snapshot_date, NULL)) AS days_with_price,
          MIN(price_min) AS price_min_min,
          MAX(price_max) AS price_max_max,
          ARRAY_AGG(price_min IGNORE NULLS ORDER BY snapshot_date)[SAFE_OFFSET(0)]
            AS price_min_first,
          ARRAY_AGG(price_min IGNORE NULLS ORDER BY snapshot_date DESC)[SAFE_OFFSET(0)]
            AS price_min_last,
          STRING_AGG(DISTINCT NULLIF(price_type, ''), '|'
                     ORDER BY NULLIF(price_type, '')) AS price_types,
          ANY_VALUE(price_currency) AS price_currency
        FROM (
          SELECT *,
            REGEXP_EXTRACT(_FILE_NAME, r'dt=([0-9]{4}-[0-9]{2}-[0-9]{2})') AS snapshot_date
          FROM tmhist
        )
        GROUP BY event_id
    """
    return bq_rows(sql, project, external_table_definition=_external_def(uris))


def load_event_series(project: str, uris: list[str],
                      event_ids: list[str]) -> list[dict[str, str]]:
    """Per-snapshot price rows for a handful of events (for plotting)."""

    if not event_ids:
        return []
    in_list = ",".join("'" + e.replace("'", "") + "'" for e in event_ids)
    sql = f"""
        SELECT event_id,
               REGEXP_EXTRACT(_FILE_NAME, r'dt=([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})')
                 AS snapshot_date,
               price_min, price_max
        FROM tmhist
        WHERE event_id IN ({in_list})
        ORDER BY event_id, snapshot_date
    """
    return bq_rows(sql, project, external_table_definition=_external_def(uris))


# ---------------------------------------------------------------------------
# Pure transforms (unit-tested offline; no network)
# ---------------------------------------------------------------------------

def _to_int(v: str | None) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _to_float(v: str | None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_metrics(rows: list[dict], total_days: int,
                    as_of: str | None) -> list[dict]:
    """Add completeness metrics + flags to each per-event row (coerced numerics)."""

    as_of_date = _parse_date(as_of)
    out = []
    for r in rows:
        snapshot_days = _to_int(r.get("snapshot_days"))
        days_with_price = _to_int(r.get("days_with_price"))
        first = _to_float(r.get("price_min_first"))
        last = _to_float(r.get("price_min_last"))
        headliner = (r.get("attraction_names") or "").split("|")[0].strip()
        price_completeness = round(days_with_price / snapshot_days, 3) if snapshot_days else 0.0
        coverage_span = round(snapshot_days / total_days, 3) if total_days else 0.0
        show_date = _parse_date(r.get("show_date"))
        out.append({
            **r,
            "snapshot_days": snapshot_days,
            "days_with_price": days_with_price,
            "price_min_first": first,
            "price_min_last": last,
            "headliner": headliner,
            "price_completeness": price_completeness,
            "coverage_span": coverage_span,
            "is_complete": coverage_span >= COMPLETE_SPAN and price_completeness >= COMPLETE_PRICE,
            "price_changed": first is not None and last is not None and first != last,
            "is_edm": (r.get("genre") or "") in EDM_GENRES,
            "days_to_show": (show_date - as_of_date).days
            if (show_date and as_of_date) else None,
        })
    return out


def enrich_geo(rows: list[dict], geo: GeoLookup) -> list[dict]:
    """Resolve each event's venue to a DMA / metro (and a Bay Area flag)."""

    for r in rows:
        hit = geo.resolve(zip_code=r.get("venue_postal_code"),
                          state=r.get("venue_state_code"))
        r["dma"] = hit.dma if hit else ""
        r["geo_code"] = hit.geo_code if hit else ""
        r["metro"] = hit.dma_name if hit else ""
        r["is_bay_area"] = bool(hit and hit.dma == SF_DMA)
    return rows


def coverage_by_dimension(rows: list[dict], key: str, top: int = 15) -> list[dict]:
    """Roll completeness up by a key (genre/state/dma/headliner)."""

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key) or "(none)"), []).append(r)
    out = []
    for label, items in groups.items():
        n = len(items)
        n_complete = sum(1 for i in items if i["is_complete"])
        n_any_price = sum(1 for i in items if i["days_with_price"] > 0)
        out.append({
            "dimension": key,
            "value": label,
            "n_events": n,
            "n_any_price": n_any_price,
            "n_complete": n_complete,
            "pct_complete": round(100 * n_complete / n, 1) if n else 0.0,
            "median_days_with_price": _median([i["days_with_price"] for i in items]),
        })
    out.sort(key=lambda d: (-d["n_complete"], -d["n_events"], d["value"]))
    return out[:top]


def pricetype_breakdown(rows: list[dict]) -> list[dict]:
    """Count events by their distinct price_type set (face vs any resale)."""

    counts: dict[str, int] = {}
    for r in rows:
        if r["days_with_price"] == 0:
            continue
        counts[r.get("price_types") or "(unspecified)"] = (
            counts.get(r.get("price_types") or "(unspecified)", 0) + 1)
    return sorted(({"price_types": k, "n_events": v} for k, v in counts.items()),
                  key=lambda d: -d["n_events"])


def days_with_price_histogram(rows: list[dict], total_days: int) -> list[dict]:
    """Distribution of priced-day counts across all events (non-overlapping bins)."""

    n = total_days
    labels = ["0", "1-4", "5-9", f"10-{n - 1}", f"{n} (all)"]
    counts = [0, 0, 0, 0, 0]

    def bin_index(d: int) -> int:
        if d <= 0:
            return 0
        if d >= n:
            return 4
        if d <= 4:
            return 1
        if d <= 9:
            return 2
        return 3

    for r in rows:
        counts[bin_index(r["days_with_price"])] += 1
    return [{"days_with_price": labels[i], "n_events": counts[i]} for i in range(5)]


def select_plot_events(rows: list[dict], n: int) -> list[dict]:
    """Pick a few shows to plot: most priced-days first, with EDM + Bay examples."""

    ranked = sorted(rows, key=lambda r: (-r["days_with_price"],
                                         not r["price_changed"], r["event_id"]))
    picks = ranked[:n]
    for flag in ("is_edm", "is_bay_area"):
        if not any(p.get(flag) for p in picks):
            extra = next((r for r in ranked if r.get(flag)), None)
            if extra and extra["event_id"] not in {p["event_id"] for p in picks}:
                picks = picks[:-1] + [extra]
    return picks[:n]


def build_series_for_plot(series_rows: list[dict], event_id: str) -> list[tuple]:
    """Ordered (snapshot_date, price_min, price_max) for one event."""

    pts = [(r["snapshot_date"], _to_float(r.get("price_min")), _to_float(r.get("price_max")))
           for r in series_rows if r.get("event_id") == event_id]
    return sorted(pts, key=lambda t: t[0])


def _median(xs: list[int]) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _parse_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "show"


# ---------------------------------------------------------------------------
# Plotting + output
# ---------------------------------------------------------------------------

def plot_price_trajectory(series: list[tuple], title: str, out_path: Path) -> None:
    """Deterministic price-trajectory PNG for one show (gaps where price missing)."""

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dates = [t[0] for t in series]
    pmin = [t[1] for t in series]
    pmax = [t[2] for t in series]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(dates, pmin, marker="o", label="price_min")
    ax.plot(dates, pmax, marker="o", label="price_max")
    ax.set_title(title)
    ax.set_xlabel("snapshot date")
    ax.set_ylabel("price (USD, face value)")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, panel: list[dict], total_days: int, uris: list[str],
                   as_of: str, hist: list[dict], ptypes: list[dict],
                   by_genre: list[dict], by_state: list[dict], by_dma: list[dict],
                   plots: list[tuple]) -> None:
    n = len(panel)
    n_any = sum(1 for r in panel if r["days_with_price"] > 0)
    n_complete = sum(1 for r in panel if r["is_complete"])
    n_all_days_priced = sum(1 for r in panel if r["days_with_price"] >= total_days)
    edm = [r for r in panel if r["is_edm"]]
    bay = [r for r in panel if r["is_bay_area"]]
    dts = sorted(dt_from_uri(u) for u in uris)

    def pct(a, b):
        return f"{round(100 * a / b, 1)}%" if b else "0%"

    lines = [
        "# Ticketmaster historical price coverage",
        "",
        f"_Generated by `eda/tm_price_eda.py`, as of {as_of}, over {len(uris)} daily "
        f"snapshots ({dts[0]} … {dts[-1]}). Re-run to refresh; numbers move only as "
        "snapshots accumulate._",
        "",
        "## Headline — is price history the bottleneck?",
        "",
        f"- **{n:,}** distinct events seen across the **{total_days}** daily snapshots.",
        f"- **{n_any:,}** ({pct(n_any, n)}) carry a price on at least one day; the rest are "
        "priceless rows (worthless for a price model).",
        f"- **{n_all_days_priced:,}** are priced on **every** snapshot day they appear.",
        f"- **{n_complete:,}** ({pct(n_complete, n)}) are **complete** "
        f"(present ≥{int(COMPLETE_SPAN*100)}% of the window AND priced "
        f"≥{int(COMPLETE_PRICE*100)}% of present days) — the demo/model-ready set.",
        f"- Bay Area (DMA {SF_DMA}): {sum(1 for r in bay if r['is_complete']):,} complete of "
        f"{len(bay):,}. Dance/Electronic: "
        f"{sum(1 for r in edm if r['is_complete']):,} complete of {len(edm):,}.",
        "",
        "## Priced-day distribution",
        "",
        "| days_with_price | n_events |",
        "|---|---|",
        *[f"| {h['days_with_price']} | {h['n_events']:,} |" for h in hist],
        "",
        "## price_type (Ticketmaster = face value, not resale)",
        "",
        "| price_types | n_events |",
        "|---|---|",
        *[f"| {p['price_types']} | {p['n_events']:,} |" for p in ptypes],
        "",
        "## Most complete coverage by genre",
        "",
        "| genre | n_events | n_complete | pct_complete | median_priced_days |",
        "|---|---|---|---|---|",
        *[f"| {g['value']} | {g['n_events']:,} | {g['n_complete']:,} | "
          f"{g['pct_complete']}% | {g['median_days_with_price']} |" for g in by_genre],
        "",
        "## Most complete coverage by metro (DMA)",
        "",
        "| metro | n_events | n_complete | pct_complete | median_priced_days |",
        "|---|---|---|---|---|",
        *[f"| {d['value']} | {d['n_events']:,} | {d['n_complete']:,} | "
          f"{d['pct_complete']}% | {d['median_days_with_price']} |" for d in by_dma],
        "",
        "## Sample price trajectories",
        "",
    ]
    for title, rel in plots:
        lines += [f"**{title}**", "", f"![{title}]({rel})", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--start-date", default=None, help="earliest dt= (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="latest dt= (YYYY-MM-DD)")
    parser.add_argument("--plot-top-n", type=int, default=6)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    plots_dir = out_dir / "plots"
    as_of = utc_now_iso()

    uris = list_snapshot_uris(args.project, args.start_date, args.end_date)
    if not uris:
        print("[tm_price_eda] no snapshot parquet files found", file=sys.stderr)
        return 1
    total_days = len({dt_from_uri(u) for u in uris})
    as_of_date = max(dt_from_uri(u) for u in uris)
    print(f"[tm_price_eda] {len(uris)} snapshots, {total_days} distinct days "
          f"(through {as_of_date})")

    geo = GeoLookup()
    panel = compute_metrics(load_event_panel(args.project, uris), total_days, as_of_date)
    panel = enrich_geo(panel, geo)

    hist = days_with_price_histogram(panel, total_days)
    ptypes = pricetype_breakdown(panel)
    by_genre = coverage_by_dimension(panel, "genre")
    by_state = coverage_by_dimension(panel, "venue_state_code")
    by_dma = coverage_by_dimension(panel, "metro")

    # Plots for a few of the most complete shows.
    plot_events = select_plot_events(panel, args.plot_top_n)
    series_rows = load_event_series(args.project, uris, [e["event_id"] for e in plot_events])
    plots: list[tuple] = []
    for ev in plot_events:
        series = build_series_for_plot(series_rows, ev["event_id"])
        if not series:
            continue
        fname = f"price_{ev['event_id']}_{_slug(ev.get('event_name'))}.png"
        title = (f"{ev.get('event_name')} — {ev.get('venue_city')} "
                 f"({ev['days_with_price']}/{ev['snapshot_days']} days priced)")
        plot_price_trajectory(series, title, plots_dir / fname)
        plots.append((title, f"plots/{fname}"))

    # Stamp every panel row with provenance for run-to-run diffing.
    for r in panel:
        r["as_of_ts"] = as_of
        r["snapshot_window"] = f"{min(dt_from_uri(u) for u in uris)}..{as_of_date}"

    panel_fields = [
        "event_id", "event_name", "headliner", "genre", "venue_name", "venue_city",
        "venue_state_code", "metro", "dma", "geo_code", "show_date", "days_to_show",
        "snapshot_days", "days_with_price", "price_completeness", "coverage_span",
        "is_complete", "price_min_first", "price_min_last", "price_min_min",
        "price_max_max", "price_changed", "price_types", "price_currency",
        "is_edm", "is_bay_area", "as_of_ts", "snapshot_window",
    ]
    panel_sorted = sorted(panel, key=lambda r: (-r["days_with_price"], r["event_id"]))
    _write_csv(out_dir / "tm_price_history_by_event.csv", panel_sorted, panel_fields)
    dim_fields = ["dimension", "value", "n_events", "n_any_price", "n_complete",
                  "pct_complete", "median_days_with_price"]
    _write_csv(out_dir / "tm_price_completeness_by_genre.csv", by_genre, dim_fields)
    _write_csv(out_dir / "tm_price_completeness_by_state.csv", by_state, dim_fields)
    _write_csv(out_dir / "tm_price_completeness_by_dma.csv", by_dma, dim_fields)
    _write_csv(out_dir / "tm_price_by_price_type.csv", ptypes, ["price_types", "n_events"])
    write_markdown(out_dir / "tm_price_eda.md", panel, total_days, uris, as_of,
                   hist, ptypes, by_genre, by_state, by_dma, plots)

    n_complete = sum(1 for r in panel if r["is_complete"])
    print(f"[tm_price_eda] {len(panel):,} events; {n_complete:,} complete; "
          f"{len(plots)} plots -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
