#!/usr/bin/env python3
"""Empirical probes of Ticketmaster pricing coverage and sweep truncation.

Answers three questions raised in ``docs/collection_efficiency_review.md`` with
~100 live Discovery calls (well inside the 5k/day quota; deterministic sampling
so a re-run hits the same events while they remain upcoming):

  1. --details    Does ``events/{id}.json`` ever return ``priceRanges`` when the
                  search listing didn't? (If yes, a targeted detail poll could
                  lift price coverage; community evidence says no.)
  2. --commerce   Does the withdrawn Commerce endpoint
                  ``commerce/v2/events/{id}/offers.json`` still answer a standard
                  API key? (Docs 404 as of 2026-07; expect 4xx.)
  3. --slices     Do dense states overflow the deep-paging cap? For each
                  ``SLICE_DAYS`` window the collector uses, fetch ``size=1`` and
                  read ``page.totalElements``: a slice > PAGE_SIZE*MAX_PAGES
                  (=1000) is silently truncated by the sweep.

Writes ``eda/output/tm_price_probe.md`` (+ a CSV per section). Requires
``TICKETMASTER_API_KEY`` in the env (Secret Manager holds the deployed copy:
``gcloud secrets versions access latest --secret=ticketmaster-api-key``).

Run (repo root):
    python eda/tm_price_probe.py                  # all sections
    python eda/tm_price_probe.py --details --sample 50
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, bq_rows, fq, utc_now_iso  # noqa: E402

BASE = "https://app.ticketmaster.com"
SECONDS_BETWEEN_CALLS = 0.25  # stay under 5 req/s, same as the collector
OUT_DIR = Path(__file__).resolve().parent / "output"

# Mirror the deployed sweep's slicing so --slices measures what production does.
SLICE_DAYS = 14
DAYS_AHEAD = 180
PAGE_CAP = 200 * 5  # PAGE_SIZE * MAX_PAGES
DENSE_STATES = ["CA", "NY", "NV", "TX"]


def _get(path: str, params: dict, key: str) -> tuple[int, dict]:
    """One Discovery GET; returns (status, parsed body). Never raises on HTTP errors."""
    qs = urllib.parse.urlencode({**params, "apikey": key})
    req = urllib.request.Request(f"{BASE}{path}?{qs}", headers={"Accept": "application/json"})
    time.sleep(SECONDS_BETWEEN_CALLS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            body = json.loads(err.read().decode("utf-8"))
        except Exception:
            body = {}
        return err.code, body
    except urllib.error.URLError as err:
        return -1, {"error": str(err)}


def _write_csv(name: str, rows: list[dict]) -> Path:
    path = OUT_DIR / name
    if rows:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return path


def sample_events(project: str, dataset: str, n: int, priced: bool) -> list[dict]:
    """Deterministic sample of upcoming events with/without a price in silver.

    Ordered by event_id hash so a re-run picks the same ids while they remain
    upcoming; prefers CA so findings speak to our focus slice.
    """
    cond = "price_min IS NOT NULL" if priced else "price_min IS NULL"
    return bq_rows(
        f"SELECT event_id, event_name, venue_state_code, genre, local_date "
        f"FROM {fq(project, dataset, 'tm_events')} "
        f"WHERE local_date >= CURRENT_DATE() AND {cond} "
        f"ORDER BY (venue_state_code = 'CA') DESC, FARM_FINGERPRINT(event_id) "
        f"LIMIT {n}", project)


def probe_details(project: str, dataset: str, key: str, sample: int) -> dict:
    """events/{id}.json on unpriced (and a priced control) sample."""
    results = []
    for grp, priced in (("unpriced", False), ("priced_control", True)):
        n = sample if not priced else max(5, sample // 10)
        for ev in sample_events(project, dataset, n, priced):
            status, body = _get(f"/discovery/v2/events/{ev['event_id']}.json", {}, key)
            ranges = body.get("priceRanges") or []
            results.append({
                "group": grp, "event_id": ev["event_id"], "state": ev["venue_state_code"],
                "genre": ev["genre"], "http": status, "has_price_ranges": bool(ranges),
                "price_min": ranges[0].get("min") if ranges else None,
            })
    _write_csv("tm_price_probe_details.csv", results)
    unpriced = [r for r in results if r["group"] == "unpriced" and r["http"] == 200]
    control = [r for r in results if r["group"] == "priced_control" and r["http"] == 200]
    return {
        "unpriced_checked": len(unpriced),
        "unpriced_with_detail_price": sum(r["has_price_ranges"] for r in unpriced),
        "control_checked": len(control),
        "control_with_detail_price": sum(r["has_price_ranges"] for r in control),
    }


def probe_commerce(project: str, dataset: str, key: str) -> dict:
    """commerce/v2 offers endpoint liveness on a few priced events."""
    results = []
    for ev in sample_events(project, dataset, 5, priced=True):
        status, body = _get(f"/commerce/v2/events/{ev['event_id']}/offers.json", {}, key)
        results.append({"event_id": ev["event_id"], "http": status,
                        "body_keys": ",".join(sorted(body)[:8])})
    _write_csv("tm_price_probe_commerce.csv", results)
    return {"statuses": sorted({r["http"] for r in results})}


def probe_slices(key: str) -> dict:
    """totalElements per production date-slice for dense states; flags truncation."""
    today = date.today()
    results = []
    for state in DENSE_STATES:
        for start_off in range(0, DAYS_AHEAD, SLICE_DAYS):
            s = today + timedelta(days=start_off)
            e = min(today + timedelta(days=start_off + SLICE_DAYS - 1),
                    today + timedelta(days=DAYS_AHEAD))
            status, body = _get("/discovery/v2/events.json", {
                "countryCode": "US", "stateCode": state, "classificationName": "music",
                "startDateTime": f"{s}T00:00:00Z", "endDateTime": f"{e}T23:59:59Z",
                "size": 1, "page": 0,
            }, key)
            total = (body.get("page") or {}).get("totalElements", -1)
            results.append({"state": state, "slice_start": str(s), "slice_end": str(e),
                            "http": status, "total_elements": total,
                            "truncated": total > PAGE_CAP})
    _write_csv("tm_price_probe_slices.csv", results)
    trunc = [r for r in results if r["truncated"]]
    return {"slices_checked": len(results), "truncated_slices": len(trunc),
            "worst": max((r["total_elements"] for r in results), default=0),
            "truncated_detail": [f"{r['state']} {r['slice_start']} ({r['total_elements']})"
                                 for r in trunc]}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--sample", type=int, default=50, help="unpriced events to detail-check")
    for flag in ("details", "commerce", "slices"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("TICKETMASTER_API_KEY")
    if not key:
        print("TICKETMASTER_API_KEY not set", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(exist_ok=True)

    picked = [f for f in ("details", "commerce", "slices") if getattr(args, f)]
    sections = picked or ["details", "commerce", "slices"]
    stamp = utc_now_iso()
    summary: dict[str, dict] = {}
    for name in sections:
        fn = {"details": lambda: probe_details(args.project, args.dataset, key, args.sample),
              "commerce": lambda: probe_commerce(args.project, args.dataset, key),
              "slices": lambda: probe_slices(key)}[name]
        summary[name] = fn()
        print(f"[{name}] {summary[name]}")

    md = [f"# TM price probe — {stamp}", "",
          "Raw rows in `tm_price_probe_{details,commerce,slices}.csv`.", ""]
    for name, s in summary.items():
        md += [f"## {name}", "```json", json.dumps(s, indent=2), "```", ""]
    (OUT_DIR / "tm_price_probe.md").write_text("\n".join(md), encoding="utf-8")
    print(f"[tm_price_probe] wrote {OUT_DIR / 'tm_price_probe.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
