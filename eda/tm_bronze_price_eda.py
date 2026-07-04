#!/usr/bin/env python3
"""Bronze-level Ticketmaster ``priceRanges`` anatomy: primary vs resale fill rates.

The silver tables keep ONE preferred price range per event (``standard`` first),
so only the raw bronze JSON can answer: *out of everything the Discovery API
returned to us, how often is a priceRanges array present at all, which ``type``s
appear (standard / resale / other), and how complete are their min/max?* Also
checks two adjacent questions raised in the 2026-07 collection review
(``docs/collection_efficiency_review.md``):

  * onsale timing — are events observed BEFORE their public onsale less likely
    to carry a price (i.e. is "watch unpriced events around onsale" a useful
    heuristic)?
  * lineup presence — how often raw events carry attractions (the headliner-gap
    source ceiling), for corroboration against the silver-side diagnosis.

Samples a deterministic set of capture days (first run stamp of each day, all
states) so a re-run counts the identical files; streams each blob and discards
it (no local copy of bronze).

Run (repo root, music-demand env, ADC authed):
    python eda/tm_bronze_price_eda.py                    # default 4 sample days
    python eda/tm_bronze_price_eda.py --days 2026-06-12 2026-07-01
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DEFAULT_PROJECT, utc_now_iso  # noqa: E402

SOURCE_PREFIX = "ticketmaster/dt="
DEFAULT_DAYS = ["2026-06-12", "2026-06-20", "2026-06-27", "2026-07-01"]
OUT_DIR = Path(__file__).resolve().parent / "output"
_STAMP = re.compile(r"_(\d{8}T\d{6}Z)\.json$")


def first_run_blobs(client, bucket: str, day: str):
    """All state files of the day's FIRST run stamp (one complete nationwide sweep)."""
    blobs = [b for b in client.list_blobs(bucket, prefix=f"{SOURCE_PREFIX}{day}/")
             if b.name.endswith(".json")]
    stamps = sorted({m.group(1) for b in blobs if (m := _STAMP.search(b.name))})
    if not stamps:
        return []
    return [b for b in blobs if f"_{stamps[0]}.json" in b.name]


def classify_event(event: dict, capture_day: str) -> dict:
    """Flatten one raw event into the fill-rate facts we aggregate."""
    ranges = event.get("priceRanges") or []
    types = sorted({r.get("type") or "untyped" for r in ranges})
    by_type = {}
    for r in ranges:
        t = r.get("type") or "untyped"
        by_type.setdefault(t, {"min": r.get("min") is not None,
                               "max": r.get("max") is not None})
    attractions = ((event.get("_embedded") or {}).get("attractions")) or []
    sale_start = (((event.get("sales") or {}).get("public")) or {}).get("startDateTime")
    pre_onsale = bool(sale_start and sale_start[:10] > capture_day)
    return {
        "event_id": event.get("id"),
        "has_ranges": bool(ranges),
        "n_ranges": len(ranges),
        "types": "|".join(types),
        "has_standard": "standard" in by_type,
        "standard_min_filled": by_type.get("standard", {}).get("min", False),
        "has_resale": any("resale" in t.lower() for t in by_type),
        "pre_onsale": pre_onsale,
        "has_attractions": bool(attractions),
    }


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    pct = lambda k: round(100 * sum(r[k] for r in rows) / n, 1) if n else 0.0  # noqa: E731
    pre = [r for r in rows if r["pre_onsale"]]
    post = [r for r in rows if not r["pre_onsale"]]

    def priced_pct(group: list[dict]) -> float:
        return round(100 * sum(r["has_ranges"] for r in group) / len(group), 1) if group else 0.0

    return {
        "events": n,
        "pct_with_price_ranges": pct("has_ranges"),
        "pct_with_standard": pct("has_standard"),
        "pct_standard_min_filled": pct("standard_min_filled"),
        "pct_with_resale": pct("has_resale"),
        "pct_with_attractions": pct("has_attractions"),
        "pre_onsale_events": len(pre),
        "pct_priced_pre_onsale": priced_pct(pre),
        "pct_priced_post_onsale": priced_pct(post),
        "type_combinations": dict(Counter(r["types"] for r in rows if r["types"]).most_common(10)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--days", nargs="*", default=DEFAULT_DAYS,
                        help="capture days (dt= partitions) to sample")
    args = parser.parse_args()

    from google.cloud import storage
    client = storage.Client(project=args.project)
    bucket = f"{args.project}-raw"

    per_day: dict[str, dict] = {}
    all_rows: dict[str, dict] = {}  # event_id -> latest row (dedupe across days/pages)
    for day in args.days:
        blobs = first_run_blobs(client, bucket, day)
        rows: dict[str, dict] = {}
        for blob in blobs:
            events = json.loads(blob.download_as_text())
            for ev in events if isinstance(events, list) else []:
                row = classify_event(ev, day)
                if not row["event_id"]:
                    continue
                # within a day prefer the priced occurrence (pagination dups)
                cur = rows.get(row["event_id"])
                if cur is None or (not cur["has_ranges"] and row["has_ranges"]):
                    rows[row["event_id"]] = row
        per_day[day] = aggregate(list(rows.values()))
        per_day[day]["files"] = len(blobs)
        all_rows.update(rows)
        print(f"[{day}] files={len(blobs)} {json.dumps(per_day[day], default=str)[:200]}...")

    overall = aggregate(list(all_rows.values()))
    OUT_DIR.mkdir(exist_ok=True)
    stamp = utc_now_iso()

    with (OUT_DIR / "tm_bronze_price_by_day.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = ["day"] + [k for k in next(iter(per_day.values())) if k != "type_combinations"]
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for day, agg in per_day.items():
            writer.writerow({"day": day, **agg})

    md = [f"# TM bronze priceRanges anatomy — {stamp}", "",
          f"Sampled capture days: {', '.join(args.days)} (first full sweep of each day).", "",
          "## Overall (events deduped across sampled days)", "```json",
          json.dumps(overall, indent=2), "```", "", "## Per day", ""]
    for day, agg in per_day.items():
        md += [f"### {day}", "```json", json.dumps(agg, indent=2), "```", ""]
    (OUT_DIR / "tm_bronze_price_eda.md").write_text("\n".join(md), encoding="utf-8")
    print(f"[tm_bronze_price_eda] overall: {json.dumps(overall, indent=2)}")
    print(f"[tm_bronze_price_eda] wrote {OUT_DIR / 'tm_bronze_price_eda.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
