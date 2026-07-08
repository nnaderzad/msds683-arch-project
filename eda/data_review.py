#!/usr/bin/env python3
"""Data review report: bronze inventory, raw samples, coverage, cross-source overlap.

Writes ``eda/output/data_review.md`` — the teammate-facing snapshot of what every
data source's raw form looks like, how fresh/complete each layer is, and what the
new Bay-Area sources (19hz, RA, ticket pages) add over Ticketmaster. Deterministic
given the data: identical bronze + BigQuery + committed CSV state reproduces an
identical report (only the provenance timestamp differs). No LLM at runtime.

Sections:
  bronze    per-source partition inventory + raw-format samples (reads GCS)
  coverage  silver/gold freshness, TM pricing, Trends targeting (reads BigQuery,
            reusing the query logic in eda/collection_sizing.py)
  overlap   Bay Area cross-source match: 19hz / RA / TM on (venue, date), pricing
            fill per source, unique fields (reads the committed CSVs + BigQuery)

Run (repo root, music-demand env, ADC authed):
    python eda/data_review.py                  # full report
    python eda/data_review.py --skip-bronze    # offline-ish: skip the GCS sections
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

EDA_DIR = Path(__file__).resolve().parent
REPO_ROOT = EDA_DIR.parent
sys.path.insert(0, str(EDA_DIR))
sys.path.insert(0, str(REPO_ROOT))

import collection_sizing as sizing  # noqa: E402
from _common import DEFAULT_DATASET, DEFAULT_PROJECT, bq_rows, fq, read_csv, utc_now_iso  # noqa: E402
from common.keys import normalize_name  # noqa: E402

OUTPUT_MD = EDA_DIR / "output" / "data_review.md"
NINETEENHZ_CSV = EDA_DIR / "output" / "nineteenhz_events.csv"
RA_CSV = EDA_DIR / "output" / "ra_events.csv"
TICKETPAGE_CSV = EDA_DIR / "output" / "ticketpage_offers.csv"

BAY_AREA_DMA = "807"

# Bronze sources and, for google_trends, the filename families (one file = one call).
SOURCES = ["ticketmaster", "google_trends", "youtube", "nineteenhz", "ra", "ticketpages"]
TRENDS_FAMILIES = [
    ("iot national (daily series, geo=US)", "_iot_US_"),
    ("iot per-DMA (daily series, geo=US-XX-DMA)", "_iot_US-"),
    ("ibr DMA snapshot (cross-DMA cross-section)", "_ibr_DMA_"),
]


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline in tests/test_data_review.py)
# ---------------------------------------------------------------------------

def truncate(obj, max_list: int = 3, max_str: int = 160, max_depth: int = 8,
             _depth: int = 0):
    """Recursively shrink a parsed-JSON object for display.

    Lists keep their first ``max_list`` items (+ a ``"... N more"`` marker),
    strings are cut at ``max_str`` chars with an ellipsis, and containers deeper
    than ``max_depth`` collapse to a size summary. Dict keys/order are
    preserved. Pure — same input, same output.
    """
    if isinstance(obj, dict):
        if _depth >= max_depth:
            return f"{{… {len(obj)} keys}}"
        return {k: truncate(v, max_list, max_str, max_depth, _depth + 1)
                for k, v in obj.items()}
    if isinstance(obj, list):
        if _depth >= max_depth:
            return f"[… {len(obj)} items]"
        head = [truncate(v, max_list, max_str, max_depth, _depth + 1)
                for v in obj[:max_list]]
        if len(obj) > max_list:
            head.append(f"... {len(obj) - max_list} more items")
        return head
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + "…"
    return obj


def drop_keys(obj, names: frozenset[str]):
    """Recursively remove display-noise keys (e.g. TM 'images', '_links'). Pure."""
    if isinstance(obj, dict):
        return {k: drop_keys(v, names) for k, v in obj.items() if k not in names}
    if isinstance(obj, list):
        return [drop_keys(v, names) for v in obj]
    return obj


def norm_venue(name: str | None) -> str:
    """Venue-name match key across sources: casefold, strip punctuation + 'the '.

    Same spirit as common/keys.normalize_name (the artist key) but venues need a
    bit more: 'The Great Northern' (TM) must meet 'Great Northern' (19hz), and
    'Public Works (Loft)' should meet 'Public Works'.
    """
    base = normalize_name(name)
    if "(" in base:  # drop room/annex qualifiers: 'public works (loft)'
        base = base.split("(", 1)[0]
    cleaned = "".join(ch if (ch.isalnum() or ch == " ") else " " for ch in base)
    words = [w for w in cleaned.split() if w]
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def overlap_sets(
    hz_keys: set[tuple[str, str]],
    ra_keys: set[tuple[str, str]],
    tm_keys: set[tuple[str, str]],
    window: tuple[str, str],
) -> dict[str, int]:
    """Classify (venue, date) keys inside the shared date window by source presence."""
    lo, hi = window
    in_win = lambda k: lo <= k[1] <= hi  # noqa: E731 — tiny local predicate
    hz = {k for k in hz_keys if in_win(k)}
    ra = {k for k in ra_keys if in_win(k)}
    tm = {k for k in tm_keys if in_win(k)}
    return {
        "19hz_in_window": len(hz),
        "ra_in_window": len(ra),
        "tm_in_window": len(tm),
        "only_19hz": len(hz - ra - tm),
        "only_ra": len(ra - hz - tm),
        "only_tm": len(tm - hz - ra),
        "hz_and_tm": len(hz & tm),
        "ra_and_tm": len(ra & tm),
        "hz_and_ra": len(hz & ra),
        "all_three": len(hz & ra & tm),
        "union": len(hz | ra | tm),
    }


def shared_window(*date_ranges: tuple[str, str]) -> tuple[str, str]:
    """Intersection of (min_date, max_date) ranges — the fair comparison window."""
    lo = max(r[0] for r in date_ranges)
    hi = min(r[1] for r in date_ranges)
    return lo, hi


def md_table(rows: list[dict], columns: list[str] | None = None) -> list[str]:
    """Render row-dicts as a GitHub markdown table (all values str()-ed)."""
    if not rows:
        return ["*(no rows)*"]
    cols = columns or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
    return out


def pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "—"


# ---------------------------------------------------------------------------
# Bronze inventory + raw samples (GCS)
# ---------------------------------------------------------------------------

def list_bronze(project: str) -> dict[str, list]:
    """Per source: sorted [(dt, n_files, bytes)] from one GCS listing pass each."""
    from google.cloud import storage

    client = storage.Client(project=project)
    bucket = f"{project}-raw"
    inventory: dict[str, list] = {}
    for source in SOURCES:
        per_dt: dict[str, list[int]] = {}
        for blob in client.list_blobs(bucket, prefix=f"{source}/"):
            parts = blob.name.split("/")
            if len(parts) < 3 or not parts[1].startswith("dt="):
                continue
            dt = parts[1][3:]
            agg = per_dt.setdefault(dt, [0, 0])
            agg[0] += 1
            agg[1] += int(blob.size or 0)
        inventory[source] = sorted((dt, n, b) for dt, (n, b) in per_dt.items())
    return inventory


def inventory_section(inventory: dict[str, list]) -> list[str]:
    lines = ["## Bronze inventory (`gs://<project>-raw/<source>/dt=<UTC-day>/`)", ""]
    rows = []
    for source, parts in inventory.items():
        if not parts:
            rows.append({"source": source, "partitions": 0, "first": "—", "last": "—",
                         "files_latest": "—", "total_MiB": "0"})
            continue
        total_bytes = sum(b for _, _, b in parts)
        rows.append({
            "source": source,
            "partitions": len(parts),
            "first": parts[0][0],
            "last": parts[-1][0],
            "files_latest": parts[-1][1],
            "total_MiB": f"{total_bytes / 2**20:,.1f}",
        })
    lines += md_table(rows)
    lines += ["", "Google Trends calls/day = files/day (one file per API call; the same",
              "ledger as `google_trends_api/check_call_rate.py`). Last 7 partitions:", ""]
    gt = inventory.get("google_trends", [])[-7:]
    lines += md_table([{"dt (UTC)": d, "calls": n} for d, n, _ in gt])
    return lines


def _newest_blob(client, bucket: str, prefix: str, contains: str | None = None,
                 suffix: str | None = None):
    """Deterministic sample pick: lexicographically last matching blob name."""
    names = [b.name for b in client.list_blobs(bucket, prefix=prefix)
             if (contains is None or contains in b.name)
             and (suffix is None or b.name.endswith(suffix))]
    if not names:
        return None
    return client.bucket(bucket).blob(sorted(names)[-1])


def _json_sample(blob, pick, **trunc_kwargs) -> list[str]:
    payload = json.loads(blob.download_as_text())
    excerpt = truncate(pick(payload), **trunc_kwargs)
    return [f"`gs://{blob.bucket.name}/{blob.name}`", "", "```json",
            json.dumps(excerpt, indent=2, ensure_ascii=False), "```", ""]


def samples_section(project: str) -> list[str]:
    from google.cloud import storage

    client = storage.Client(project=project)
    bucket = f"{project}-raw"
    lines = ["## Raw-format samples (newest capture per source family)", ""]

    tm = _newest_blob(client, bucket, "ticketmaster/", contains="_CA_")
    if tm:
        lines += ["### Ticketmaster — one Discovery event (of a per-state JSON array; "
                  "`images`/`_links` omitted for brevity)", ""]
        lines += _json_sample(
            tm, lambda p: drop_keys(p[0], frozenset({"images", "_links"})) if p else {},
            max_list=2, max_str=120, max_depth=5)

    for label, marker in TRENDS_FAMILIES:
        blob = _newest_blob(client, bucket, "google_trends/", contains=marker)
        if blob:
            lines += [f"### Google Trends — {label}", ""]
            lines += _json_sample(blob, lambda p: p)

    yt = _newest_blob(client, bucket, "youtube/")
    if yt:
        lines += ["### YouTube — daily channel-stats rollup", ""]
        lines += _json_sample(yt, lambda p: p)

    hz = _newest_blob(client, bucket, "nineteenhz/", suffix=".html")
    if hz:
        html = hz.download_as_text()
        idx = max(html.find("<tr"), 0)
        lines += ["### 19hz.info — raw listing HTML (bronze is the untouched page)", "",
                  f"`gs://{bucket}/{hz.name}` (excerpt at first `<tr>`)", "",
                  "```html", html[idx:idx + 1200], "```", "",
                  "Parsed row (committed `eda/output/nineteenhz_events.csv`):", ""]
        hz_rows = read_csv(NINETEENHZ_CSV)
        if hz_rows:
            lines += ["```json", json.dumps(truncate(hz_rows[0]), indent=2,
                                            ensure_ascii=False), "```", ""]

    ra = _newest_blob(client, bucket, "ra/", suffix=".json")
    if ra:
        lines += ["### Resident Advisor — GraphQL eventListings response", ""]
        lines += _json_sample(ra, lambda p: p, max_list=2, max_depth=9)

    tp = _newest_blob(client, bucket, "ticketpages/", suffix=".json")
    if tp:
        lines += ["### Ticket pages — schema.org JSON-LD offers (first page payload)", ""]
        lines += _json_sample(tp, lambda p: p[0] if isinstance(p, list) and p else p,
                              max_list=2, max_depth=6)

    return lines


# ---------------------------------------------------------------------------
# Coverage (BigQuery, reusing collection_sizing)
# ---------------------------------------------------------------------------

def coverage_section(project: str, dataset: str) -> list[str]:
    lines = ["## Silver/gold coverage", "",
             "### Freshness (`MAX(snapshot_date)` per fact table)", ""]
    lines += md_table(sizing.freshness_rows(project, dataset))

    pricing = sizing.pricing_rows(project, dataset)
    lines += ["", "### Ticketmaster pricing coverage (tm_observations, honest history)", ""]
    lines += md_table(pricing["coverage"])
    lines += ["", "Priced-from-first-observation split (why re-polling unpriced events is pointless):", ""]
    lines += md_table(pricing["day1_split"])

    lines += ["", "### Headliner resolution (caps every Trends/YouTube join)", ""]
    lines += md_table(bq_rows(
        f"SELECT COUNT(DISTINCT t.event_id) AS priced_upcoming, "
        f"COUNT(DISTINCT b.event_id) AS with_headliner, "
        f"ROUND(COUNT(DISTINCT b.event_id)/COUNT(DISTINCT t.event_id)*100, 1) AS pct "
        f"FROM {fq(project, dataset, 'tm_events')} t "
        f"LEFT JOIN {fq(project, dataset, 'bridge_event_artist')} b "
        f"ON b.event_id = t.event_id AND b.is_headliner "
        f"WHERE t.price_min IS NOT NULL AND t.local_date >= CURRENT_DATE()", project))

    lines += ["", "### Google Trends targeting (upcoming headliner×DMA pairs by tier)", ""]
    lines += md_table(sizing.pairs_rows(project, dataset))
    return lines


# ---------------------------------------------------------------------------
# Bay Area cross-source overlap + unique fields
# ---------------------------------------------------------------------------

def tm_bay_area_rows(project: str, dataset: str) -> list[dict[str, str]]:
    return bq_rows(
        f"SELECT v.venue_name, CAST(e.show_date AS STRING) AS show_date, "
        f"MAX(IF(t.price_min IS NOT NULL, 1, 0)) AS priced "
        f"FROM {fq(project, dataset, 'dim_event')} e "
        f"JOIN {fq(project, dataset, 'dim_venue')} v USING (venue_id) "
        f"LEFT JOIN {fq(project, dataset, 'tm_events')} t ON t.event_id = e.event_id "
        f"WHERE v.dma_code = '{BAY_AREA_DMA}' AND e.show_date >= CURRENT_DATE() "
        f"GROUP BY 1, 2", project)


def overlap_section(project: str, dataset: str) -> list[str]:
    hz_rows = read_csv(NINETEENHZ_CSV)
    ra_rows = read_csv(RA_CSV)
    tp_rows = read_csv(TICKETPAGE_CSV)
    tm_rows = tm_bay_area_rows(project, dataset)

    key = lambda venue, date: (norm_venue(venue), date)  # noqa: E731
    hz_keys = {key(r["venue"], r["event_date"]) for r in hz_rows if r["event_date"]}
    ra_keys = {key(r["venue"], r["event_date"]) for r in ra_rows if r["event_date"]}
    tm_keys = {key(r["venue_name"], r["show_date"]) for r in tm_rows}

    ranges = [(min(d for _, d in ks), max(d for _, d in ks))
              for ks in (hz_keys, ra_keys, tm_keys)]
    window = shared_window(*ranges)
    counts = overlap_sets(hz_keys, ra_keys, tm_keys, window)

    lines = ["## Bay Area cross-source overlap (19hz vs RA vs Ticketmaster)", "",
             f"Match key: (normalized venue name, event date). Shared date window: "
             f"**{window[0]} .. {window[1]}** (RA's single daily request returns one "
             f"100-event page ≈ 2.5 weeks ahead, which bounds the window).", ""]
    lines += md_table([
        {"metric": m, "events": n} for m, n in counts.items()
    ], columns=["metric", "events"])
    lines += ["", "Venue-name matching is exact-after-normalization — venue aliases "
              "('The Endup' vs 'EndUp') under-count matches slightly; treat the "
              "only-in-X numbers as upper bounds.", ""]

    # Pricing fill per source (whole pulls, not just the shared window).
    hz_priced = sum(1 for r in hz_rows if r["price_min"])
    hz_free = sum(1 for r in hz_rows if r["is_free"] == "True")
    ra_cost = sum(1 for r in ra_rows if r["cost_text"] not in ("", "0"))
    tm_priced = sum(1 for r in tm_rows if r["priced"] == "1")
    tp_priced = sum(1 for r in tp_rows if r["price_min"])
    lines += ["### Pricing fill per source", ""]
    lines += md_table([
        {"source": "19hz (Bay Area listing)", "events": len(hz_rows),
         "with_price": f"{hz_priced} ({pct(hz_priced, len(hz_rows))})",
         "note": f"+{hz_free} explicitly free"},
        {"source": "RA (area 218, 1 page)", "events": len(ra_rows),
         "with_price": f"{ra_cost} ({pct(ra_cost, len(ra_rows))})",
         "note": "cost_text, unparsed"},
        {"source": "TM (DMA 807 upcoming)", "events": len(tm_rows),
         "with_price": f"{tm_priced} ({pct(tm_priced, len(tm_rows))})",
         "note": "priceRanges, primary only"},
        {"source": "ticket pages (JSON-LD)", "events": len(tp_rows),
         "with_price": f"{tp_priced} ({pct(tp_priced, len(tp_rows))})",
         "note": "offer rows incl. availability"},
    ])

    # Unique fields the new sources bring.
    hz_genres = sum(1 for r in hz_rows if r["genres"])
    hz_age = sum(1 for r in hz_rows if r["age_restriction"])
    hz_artists = sum(int(r["n_artists"] or 0) for r in hz_rows)
    attending = sorted((int(r["attending"] or 0), r["title"], r["event_date"])
                       for r in ra_rows)[::-1]
    ra_attending_total = sum(a for a, _, _ in attending)
    avail = Counter(r["availability"] for r in tp_rows if r["availability"])
    lines += ["", "### Unique fields the new sources add", ""]
    lines += md_table([
        {"source": "19hz", "field": "genres", "fill": pct(hz_genres, len(hz_rows)),
         "example/note": "genre tags per event (TM genre is sparse/coarse)"},
        {"source": "19hz", "field": "full lineup", "fill": "100%",
         "example/note": f"{hz_artists} artist credits across {len(hz_rows)} events (b2b split)"},
        {"source": "19hz", "field": "age_restriction", "fill": pct(hz_age, len(hz_rows)),
         "example/note": "18+/21+"},
        {"source": "19hz", "field": "is_free", "fill": pct(hz_free, len(hz_rows)),
         "example/note": "explicit free-event flag"},
        {"source": "RA", "field": "attending", "fill": "100%",
         "example/note": f"total {ra_attending_total} across {len(ra_rows)} events — "
                         f"a per-event social demand signal no other source has"},
        {"source": "RA", "field": "genres", "fill":
            pct(sum(1 for r in ra_rows if r["genres"]), len(ra_rows)),
         "example/note": "curated electronic subgenres"},
        {"source": "ticket pages", "field": "availability", "fill":
            pct(sum(avail.values()), len(tp_rows)),
         "example/note": ", ".join(f"{k.rsplit('/', 1)[-1]}×{v}" for k, v in
                                   sorted(avail.items())) or "—"},
    ])
    lines += ["", "Top-5 RA events by `attending` (the demand-signal preview):", ""]
    lines += md_table([
        {"attending": a, "title": t, "date": d} for a, t, d in attending[:5]
    ])
    return lines


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def build_report(project: str, dataset: str, skip_bronze: bool) -> str:
    lines = ["# Data review — bronze → gold, all sources", "",
             f"Generated {utc_now_iso()} by `eda/data_review.py` "
             f"(project `{project}`, dataset `{dataset}`). Re-run the same command "
             f"to refresh; narrative companion: `eda/data_review_2026-07.md`.", ""]
    if not skip_bronze:
        inventory = list_bronze(project)
        lines += inventory_section(inventory) + [""]
        lines += samples_section(project) + [""]
    lines += coverage_section(project, dataset) + [""]
    lines += overlap_section(project, dataset) + [""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--skip-bronze", action="store_true",
                        help="skip the GCS inventory/samples sections (BQ + CSVs only)")
    parser.add_argument("--output", default=str(OUTPUT_MD))
    args = parser.parse_args()

    report = build_report(args.project, args.dataset, args.skip_bronze)
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"[data_review] wrote {len(report.splitlines())} lines -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
