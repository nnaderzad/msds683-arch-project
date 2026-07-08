#!/usr/bin/env python3
"""Data review report: bronze inventory, raw samples, coverage, cross-source overlap.

Writes ``eda/output/data_review.md`` — the teammate-facing snapshot of what every
data source's raw form looks like, how fresh/complete each layer is, and what the
new Bay-Area sources (19hz, RA, ticket pages) add over Ticketmaster. Deterministic
given the data: identical bronze + BigQuery + committed CSV state reproduces an
identical report (only the provenance timestamp differs). No LLM at runtime.

Sections:
  bronze    per-source partition inventory + raw-format samples (reads GCS)
  fields    EXHAUSTIVE per-API field inventory: every raw field path the source
            actually returns, with observed JSON types + fill %, measured from
            real captures (not from API docs)
  coverage  silver/gold freshness, TM pricing, Trends targeting (reads BigQuery,
            reusing the query logic in eda/collection_sizing.py)
  overlap   Bay Area cross-source match: 19hz / RA / TM on (venue, date), pricing
            fill per source, unique fields (reads the committed CSVs + BigQuery)

--plots additionally renders PNGs into eda/output/plots/review_*.png and embeds
them in the report (matplotlib is a local-only dep, see eda/requirements.txt;
the unit tests skip it via importorskip so CI stays lean).

Run (repo root, music-demand env, ADC authed):
    python eda/data_review.py --plots          # full report + PNGs
    python eda/data_review.py --skip-bronze    # offline-ish: skip the GCS sections
    python eda/data_review.py --trace rZ7HnEZ1Af00jd   # one event, bronze -> forecast
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
DEMO_EVENT_ID = "rZ7HnEZ1Af00jd"  # Everclear @ The Independent — the traced hero show
PLOTS_DIR = EDA_DIR / "output" / "plots"

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


def _walk_paths(obj, prefix: str, seen: set[str], types: dict[str, set]) -> None:
    """Collect every field path present in one record (dotted keys, [] for lists)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk_paths(v, f"{prefix}.{k}" if prefix else k, seen, types)
    elif isinstance(obj, list):
        for v in obj:
            _walk_paths(v, f"{prefix}[]", seen, types)
    else:
        tname = {bool: "bool", int: "int", float: "float", str: "str",
                 type(None): "null"}.get(type(obj), type(obj).__name__)
        seen.add(prefix)
        types.setdefault(prefix, set()).add(tname)


def field_inventory(records: list) -> list[dict[str, str]]:
    """Exhaustive field-path inventory across raw records (pure).

    One row per distinct leaf path (``priceRanges[].min``), with the observed
    JSON types and fill = % of records where the path occurs at least once.
    Measured from real captures, so it's what the API *actually* returns —
    including fields its docs undersell (or that are always null in practice).
    """
    per_path: Counter = Counter()
    types: dict[str, set] = {}
    for rec in records:
        seen: set[str] = set()
        _walk_paths(rec, "", seen, types)
        per_path.update(seen)
    n = len(records) or 1
    return [{"field": path,
             "types": ",".join(sorted(types[path])),
             "fill": f"{per_path[path] / n * 100:.0f}%"}
            for path in sorted(per_path)]


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


def price_stats(values: list[float]) -> dict[str, str]:
    """n / quartiles / p90 summary of a price list (pure; deterministic sort)."""
    if not values:
        return {"n": "0", "p25": "—", "median": "—", "p75": "—", "p90": "—"}
    v = sorted(values)

    def q(p: float) -> str:
        return f"${v[min(len(v) - 1, int(p * len(v)))]:.0f}"

    return {"n": str(len(v)), "p25": q(0.25), "median": q(0.50),
            "p75": q(0.75), "p90": q(0.90)}


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
# Exhaustive per-API field inventory (what each API actually returns)
# ---------------------------------------------------------------------------

def _normalize_trends_records(payload: dict) -> dict:
    """Rename the artist-keyed value column ('Zedd': 55 -> '<query>': 55) so one
    field inventory covers every artist's file with the same paths."""
    q = payload.get("query")
    out = dict(payload)
    out["records"] = [
        {("<query>" if k == q else k): v for k, v in rec.items()}
        for rec in payload.get("records", [])
    ]
    return out


def fields_section(project: str) -> list[str]:
    """One exhaustive field table per API, measured from the newest real captures."""
    from google.cloud import storage

    client = storage.Client(project=project)
    bucket = f"{project}-raw"
    lines = ["## Field inventory — every raw field each API returns", "",
             "Measured from real captures (`fill` = % of sampled records where the "
             "path occurs; `[]` = list element). This is the observed contract, "
             "not the documented one.", ""]

    def emit(title: str, records: list, note: str = "") -> None:
        nonlocal lines
        lines += [f"### {title}", ""]
        if note:
            lines += [note, ""]
        lines += md_table(field_inventory(records)) + [""]

    tm = _newest_blob(client, bucket, "ticketmaster/", contains="_CA_")
    if tm:
        events = json.loads(tm.download_as_text())
        emit(f"Ticketmaster Discovery `events.json` — {len(events)} events (newest CA capture)",
             events,
             "We keep ~30 of these paths (see `flatten_event` in the TM cloud function); "
             "the rest land untouched in bronze for replay.")

    for label, marker in TRENDS_FAMILIES:
        blob = _newest_blob(client, bucket, "google_trends/", contains=marker)
        if blob:
            payload = _normalize_trends_records(json.loads(blob.download_as_text()))
            emit(f"Google Trends — {label} (1 payload, {len(payload.get('records', []))} records)",
                 [payload],
                 "`records[].<query>` is the artist-keyed 0–100 value column "
                 "(named after the search term; normalized here for the inventory).")

    yt = _newest_blob(client, bucket, "youtube/")
    if yt:
        payload = json.loads(yt.download_as_text())
        emit(f"YouTube Data API rollup — {len(payload.get('records', []))} artist records",
             payload.get("records", []))

    hz_rows = read_csv(NINETEENHZ_CSV) if NINETEENHZ_CSV.exists() else []
    if hz_rows:
        emit(f"19hz.info — parsed listing columns ({len(hz_rows)} events)",
             [{k: (v or None) for k, v in r.items()} for r in hz_rows],
             "Raw bronze is the untouched HTML page; these are the columns "
             "`collect_19hz.py` parses out of it (fill = non-empty).")

    ra = _newest_blob(client, bucket, "ra/", suffix=".json")
    if ra:
        payload = json.loads(ra.download_as_text())
        ra_events = (payload.get("response", {}).get("data", {})
                     .get("eventListings", {}).get("data", []))
        emit(f"Resident Advisor GraphQL `eventListings` — {len(ra_events)} listings",
             ra_events,
             "Fields are what our GraphQL query requests (see `LISTINGS_QUERY` in "
             "`ra_api/collect_ra.py`) — RA's schema has more, but each field must "
             "be asked for explicitly.")

    tp = _newest_blob(client, bucket, "ticketpages/", suffix=".json")
    if tp:
        payload = json.loads(tp.download_as_text())
        emit(f"Ticket-page JSON-LD — {len(payload) if isinstance(payload, list) else 1} page payloads",
             payload if isinstance(payload, list) else [payload],
             "schema.org Event/offers as embedded by eventbrite + shotgun.")

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


def overlap_data(project: str, dataset: str) -> dict:
    """Load all four sources and compute the (venue, date) overlap — shared by
    the markdown section and the --plots renderer."""
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
    return {"hz_rows": hz_rows, "ra_rows": ra_rows, "tp_rows": tp_rows,
            "tm_rows": tm_rows, "window": window, "counts": counts}


def overlap_section(data: dict) -> list[str]:
    hz_rows, ra_rows = data["hz_rows"], data["ra_rows"]
    tp_rows, tm_rows = data["tp_rows"], data["tm_rows"]
    window, counts = data["window"], data["counts"]

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
# Price distributions & trends (upcoming priced TM events + our observation window)
# ---------------------------------------------------------------------------

def price_data(project: str, dataset: str) -> dict:
    """One pass over upcoming priced TM events (+ the tm_observations daily medians).

    Per-event list price = current tm_events.price_min. Geography scopes nest:
    US ⊃ CA ⊃ Bay Area (DMA 807, via the conformed dims).
    """
    events = bq_rows(
        f"SELECT t.price_min, t.venue_state_code AS state, t.genre, "
        f"FORMAT_DATE('%Y-%m', t.local_date) AS show_month, v.dma_code "
        f"FROM {fq(project, dataset, 'tm_events')} t "
        f"LEFT JOIN {fq(project, dataset, 'dim_event')} e ON e.event_id = t.event_id "
        f"LEFT JOIN {fq(project, dataset, 'dim_venue')} v ON v.venue_id = e.venue_id "
        f"WHERE t.local_date >= CURRENT_DATE() AND t.price_min IS NOT NULL "
        f"AND t.price_min > 0", project)
    daily = bq_rows(
        f"SELECT CAST(snapshot_date AS STRING) AS d, "
        f"APPROX_QUANTILES(price_min, 100)[OFFSET(50)] AS median_price, "
        f"COUNT(*) AS n FROM {fq(project, dataset, 'tm_observations')} "
        f"WHERE price_min IS NOT NULL GROUP BY 1 ORDER BY 1", project)
    return {"events": events, "daily": daily}


def price_section(pdata: dict) -> list[str]:
    events = pdata["events"]
    price = lambda r: float(r["price_min"])  # noqa: E731
    scopes = {
        "US (all upcoming priced)": events,
        "California": [r for r in events if r["state"] == "CA"],
        f"Bay Area (DMA {BAY_AREA_DMA})": [r for r in events
                                           if r["dma_code"] == BAY_AREA_DMA],
    }
    lines = ["## Price distributions & trends (list price = current `tm_events.price_min`)",
             ""]
    lines += ["### By geography (nested scopes)", ""]
    lines += md_table([{"scope": name, **price_stats([price(r) for r in rows])}
                       for name, rows in scopes.items()])

    by_genre: dict[str, list[float]] = {}
    for r in events:
        if r["genre"]:
            by_genre.setdefault(r["genre"], []).append(price(r))
    top = sorted(by_genre.items(), key=lambda kv: -len(kv[1]))[:10]
    lines += ["", "### By genre (top 10 by upcoming priced events, US-wide)", ""]
    lines += md_table([{"genre": g, **price_stats(v)} for g, v in top])

    by_month: dict[str, list[float]] = {}
    for r in events:
        by_month.setdefault(r["show_month"], []).append(price(r))
    lines += ["", "### By show month (are later shows listed higher?)", ""]
    lines += md_table([{"show_month": m, **price_stats(v)}
                       for m, v in sorted(by_month.items())])

    lines += ["", "**Time-depth caveat:** our price archive starts **2026-06-08** "
              "(first TM sweep), so multi-year questions — e.g. \"have tickets "
              "gotten more expensive since COVID?\" — are unanswerable from our "
              "own data; the honest view we DO have is the per-day median over "
              "our observation window (plot below), which also shows how little "
              "listed prices move day-to-day (96% of shows are flat — see "
              "`eda/output/price_movement.md`). The archive answers the "
              "long-trend question a little better every day it accumulates.", ""]
    return lines


# ---------------------------------------------------------------------------
# --plots: deterministic PNGs (matplotlib is a local-only dep; CI never imports it)
# ---------------------------------------------------------------------------

def render_plots(project: str, dataset: str, inventory: dict, odata: dict,
                 pdata: dict) -> dict[str, str]:
    """Write review_*.png into eda/output/plots/; return {plot_key: relative_path}."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    def save(fig, name: str, key: str) -> None:
        fig.tight_layout()
        fig.savefig(PLOTS_DIR / name, dpi=110)
        plt.close(fig)
        written[key] = f"plots/{name}"

    # 1. Google Trends calls/day (collection throughput; the 6h-window fix is visible)
    gt = inventory.get("google_trends", [])[-24:]
    if gt:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.bar([d[5:] for d, _, _ in gt], [n for _, n, _ in gt], color="#4c78a8")
        ax.axhline(800, color="#d62728", ls="--", lw=1, label="800/day budget")
        ax.set_title("Google Trends calls per UTC day (1 bronze file = 1 call)")
        ax.set_ylabel("calls")
        ax.tick_params(axis="x", rotation=60, labelsize=7)
        ax.legend(loc="upper left", fontsize=8)
        save(fig, "review_trends_calls.png", "trends_calls")

    # 2. TM pricing coverage by observation day
    rows = bq_rows(
        f"SELECT CAST(snapshot_date AS STRING) AS d, COUNT(*) AS n, "
        f"ROUND(COUNTIF(price_min IS NOT NULL)/COUNT(*)*100, 1) AS pct "
        f"FROM {fq(project, dataset, 'tm_observations')} GROUP BY 1 ORDER BY 1", project)
    if rows:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 4.5), sharex=True)
        days = [r["d"][5:] for r in rows]
        ax1.plot(days, [float(r["pct"]) for r in rows], marker="o", ms=3, color="#4c78a8")
        ax1.set_ylabel("% obs priced")
        ax1.set_title("tm_observations: events observed & % priced, per UTC day")
        ax2.bar(days, [int(r["n"]) for r in rows], color="#9ecae9")
        ax2.set_ylabel("events observed")
        ax2.tick_params(axis="x", rotation=60, labelsize=7)
        save(fig, "review_tm_pricing_by_day.png", "tm_pricing")

    # 3. The traced demo event: Trends daily interest + observed price vs forecast
    art = bq_rows(
        f"SELECT b.artist_id FROM {fq(project, dataset, 'bridge_event_artist')} b "
        f"WHERE b.event_id = '{DEMO_EVENT_ID}' AND b.is_headliner", project)
    if art:
        aid = art[0]["artist_id"]
        trend = bq_rows(
            f"SELECT CAST(snapshot_date AS STRING) AS d, interest "
            f"FROM {fq(project, dataset, 'fact_trends_daily')} "
            f"WHERE artist_id = {aid} AND dma_code = '{BAY_AREA_DMA}' "
            f"AND NOT is_partial ORDER BY snapshot_date", project)
        obs = bq_rows(
            f"SELECT days_to_show, price_min "
            f"FROM {fq(project, dataset, 'fact_event_demand')} "
            f"WHERE event_id = '{DEMO_EVENT_ID}' AND price_min IS NOT NULL "
            f"ORDER BY days_to_show", project)
        fc = bq_rows(
            f"SELECT days_to_show, predicted_price "
            f"FROM {fq(project, dataset, 'forecast_event_price')} "
            f"WHERE event_id = '{DEMO_EVENT_ID}' ORDER BY days_to_show", project)
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.2))
        ax1.plot([r["d"] for r in trend], [int(r["interest"]) for r in trend],
                 lw=0.9, color="#4c78a8")
        ax1.set_title(f"fact_trends_daily — headliner in DMA {BAY_AREA_DMA}")
        ax1.set_ylabel("interest (0–100, per-pull scale)")
        step = max(1, len(trend) // 6)
        ax1.set_xticks(range(0, len(trend), step))
        ax1.set_xticklabels([trend[i]["d"] for i in range(0, len(trend), step)],
                            rotation=45, fontsize=7)
        ax2.plot([int(r["days_to_show"]) for r in fc],
                 [float(r["predicted_price"]) for r in fc],
                 color="#d62728", label="forecast curve")
        ax2.scatter([int(r["days_to_show"]) for r in obs],
                    [float(r["price_min"]) for r in obs],
                    s=10, color="#4c78a8", label="observed price_min")
        ax2.invert_xaxis()  # read left->right as approaching the show
        ax2.set_xlabel("days to show")
        ax2.set_ylabel("price ($)")
        ax2.set_title("observed vs forecast — demo event")
        ax2.legend(fontsize=8)
        save(fig, "review_trace_demo_event.png", "trace")

    # 4. Bay Area overlap + 5. what the new sources measure
    c = odata["counts"]
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(11, 3))
    bars = [("19hz", c["19hz_in_window"]), ("TM", c["tm_in_window"]),
            ("RA", c["ra_in_window"]), ("union", c["union"])]
    ax1.bar([b[0] for b in bars], [b[1] for b in bars],
            color=["#4c78a8", "#d62728", "#e8a838", "#54a24b"])
    ax1.set_title(f"events per source\n({odata['window'][0]} → {odata['window'][1]})")
    only = [("only\n19hz", c["only_19hz"]), ("only\nTM", c["only_tm"]),
            ("only\nRA", c["only_ra"]), ("19hz\n∩TM", c["hz_and_tm"]),
            ("19hz\n∩RA", c["hz_and_ra"]), ("all 3", c["all_three"])]
    ax2.bar([o[0] for o in only], [o[1] for o in only], color="#9ecae9")
    ax2.set_title("overlap on (venue, date)")
    ax2.tick_params(axis="x", labelsize=7)
    prices = [float(r["price_min"]) for r in odata["hz_rows"]
              if r["price_min"] and float(r["price_min"]) <= 200]
    ax3.hist(prices, bins=20, color="#54a24b")
    ax3.set_title("19hz price_min distribution (≤$200)")
    ax3.set_xlabel("$")
    save(fig, "review_bay_area_sources.png", "overlap")

    att = sorted(int(r["attending"] or 0) for r in odata["ra_rows"])
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.bar(range(len(att)), att, color="#e8a838", width=1.0)
    ax.set_title("RA `attending` per event (sorted) — the social demand signal")
    ax.set_xlabel(f"{len(att)} events, one page, area 218")
    ax.set_ylabel("attending")
    save(fig, "review_ra_attending.png", "attending")

    # 6. Price distributions & trends (2x2)
    ev = pdata["events"]
    price = lambda r: float(r["price_min"])  # noqa: E731
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    ax1, ax2, ax3, ax4 = axes.flat
    scopes = [("US", ev, "#4c78a8"),
              ("California", [r for r in ev if r["state"] == "CA"], "#e8a838"),
              (f"Bay Area (DMA {BAY_AREA_DMA})",
               [r for r in ev if r["dma_code"] == BAY_AREA_DMA], "#d62728")]
    for name, rows, color in scopes:
        vals = [price(r) for r in rows if price(r) <= 300]
        ax1.hist(vals, bins=40, density=True, histtype="step", lw=1.6,
                 color=color, label=f"{name} (n={len(rows)})")
    ax1.set_title("list price_min by geography (≤$300, density)")
    ax1.set_xlabel("$")
    ax1.legend(fontsize=8)

    by_genre: dict[str, list[float]] = {}
    for r in ev:
        if r["genre"]:
            by_genre.setdefault(r["genre"], []).append(price(r))
    top8 = sorted(by_genre.items(), key=lambda kv: -len(kv[1]))[:8]
    ax2.boxplot([[v for v in vals if v <= 500] for _, vals in top8],
                tick_labels=[g[:12] for g, _ in top8], showfliers=False)
    ax2.set_title("list price_min by genre (top 8, ≤$500, no outliers)")
    ax2.set_ylabel("$")
    ax2.tick_params(axis="x", rotation=45, labelsize=7)

    by_month: dict[str, list[float]] = {}
    for r in ev:
        by_month.setdefault(r["show_month"], []).append(price(r))
    months = sorted(by_month)
    med = [sorted(by_month[m])[len(by_month[m]) // 2] for m in months]
    ax3.bar(months, med, color="#9ecae9")
    for i, m in enumerate(months):
        ax3.text(i, med[i], f"n={len(by_month[m])}", ha="center", va="bottom",
                 fontsize=6, rotation=45)
    ax3.set_title("median list price by SHOW month (upcoming events)")
    ax3.set_ylabel("$")
    ax3.tick_params(axis="x", rotation=45, labelsize=7)

    daily = pdata["daily"]
    ax4.plot([r["d"][5:] for r in daily],
             [float(r["median_price"]) for r in daily], marker="o", ms=3,
             color="#4c78a8")
    ax4.set_title("median OBSERVED price per day (archive starts 2026-06-08)")
    ax4.set_ylabel("$")
    ax4.tick_params(axis="x", rotation=60, labelsize=6)
    save(fig, "review_price_distributions.png", "prices")

    return written


# ---------------------------------------------------------------------------
# --trace: one event, bronze -> silver -> gold -> forecast (real values)
# ---------------------------------------------------------------------------

def trace_event(project: str, dataset: str, event_id: str) -> list[str]:
    """Follow one Ticketmaster event through every layer, with actual rows.

    The narrative companion embeds one of these traces; re-run to refresh it.
    Full per-stage SQL lives in docs/transformations_showcase.md — this shows
    the *data*, not the code.
    """
    t = lambda name: fq(project, dataset, name)  # noqa: E731
    lines = [f"# Event trace — `{event_id}` (bronze → forecast)", "",
             f"Generated {utc_now_iso()} by `eda/data_review.py --trace {event_id}`.", ""]

    lines += ["## 0. Raw (bronze) — the event as Ticketmaster sends it", ""]
    from google.cloud import storage
    client = storage.Client(project=project)
    bucket = f"{project}-raw"
    blob = _newest_blob(client, bucket, "ticketmaster/", contains="_CA_")
    raw = next((e for e in json.loads(blob.download_as_text())
                if e.get("id") == event_id), None) if blob else None
    if raw:
        lines += [f"`gs://{bucket}/{blob.name}` (`images`/`_links` omitted)", "",
                  "```json",
                  json.dumps(truncate(drop_keys(raw, frozenset({"images", "_links"})),
                                      max_list=2, max_str=120, max_depth=5),
                             indent=2, ensure_ascii=False),
                  "```", ""]
    else:
        lines += ["*(event not present in the newest CA capture — raw sample skipped)*", ""]

    lines += ["## 1. Silver `tm_events` — current-state row (MERGE upsert)", ""]
    lines += md_table(bq_rows(
        f"SELECT event_id, event_name, local_date, status_code, price_min, price_max, "
        f"venue_name, attraction_names, genre, CAST(first_seen_ts_utc AS STRING) first_seen "
        f"FROM {t('tm_events')} WHERE event_id = '{event_id}'", project))

    lines += ["", "## 2. Silver `tm_observations` — honest per-day history (no forward-fill)", ""]
    obs = bq_rows(
        f"SELECT CAST(snapshot_date AS STRING) snapshot_date, price_min, price_max, "
        f"status_code, n_captures, price_disagreed "
        f"FROM {t('tm_observations')} WHERE event_id = '{event_id}' "
        f"ORDER BY snapshot_date", project)
    lines += md_table(obs[:3] + ([{"snapshot_date": f"… {len(obs) - 6} more days …"}]
                                 if len(obs) > 6 else []) + obs[-3:])

    lines += ["", "## 3. Headliner resolution (`bridge_event_artist` → `dim_artist`)", ""]
    art = bq_rows(
        f"SELECT b.artist_id, a.artist_name, b.is_headliner "
        f"FROM {t('bridge_event_artist')} b JOIN {t('dim_artist')} a USING (artist_id) "
        f"WHERE b.event_id = '{event_id}' ORDER BY b.is_headliner DESC", project)
    lines += md_table(art)
    headliner = next((r["artist_id"] for r in art if r["is_headliner"]
                      in ("true", "True", "1")), None)

    if headliner:
        lines += ["", "## 4. Demand signals for the headliner (Bay Area DMA 807)", "",
                  f"`fact_trends_daily` (artist_id={headliner}, last 5 days):", ""]
        lines += md_table(bq_rows(
            f"SELECT CAST(snapshot_date AS STRING) snapshot_date, interest, is_partial "
            f"FROM {t('fact_trends_daily')} WHERE artist_id = {headliner} "
            f"AND dma_code = '{BAY_AREA_DMA}' ORDER BY snapshot_date DESC LIMIT 5", project))
        lines += ["", "`fact_youtube` (last 3 snapshots):", ""]
        lines += md_table(bq_rows(
            f"SELECT CAST(snapshot_date AS STRING) snapshot_date, official_subscribers, "
            f"official_total_views FROM {t('fact_youtube')} WHERE artist_id = {headliner} "
            f"ORDER BY snapshot_date DESC LIMIT 3", project))

    lines += ["", "## 5. Gold `fact_event_demand` — one row per (event, snapshot day)", ""]
    lines += md_table(bq_rows(
        f"SELECT CAST(snapshot_date AS STRING) snapshot_date, days_to_show, price_min, "
        f"price_max, local_interest, yt_subscribers "
        f"FROM {t('fact_event_demand')} WHERE event_id = '{event_id}' "
        f"ORDER BY snapshot_date DESC LIMIT 5", project))

    lines += ["", "## 6. Gold `forecast_event_price` — precomputed anchor+drift curve", ""]
    lines += md_table(bq_rows(
        f"SELECT days_to_show, predicted_price "
        f"FROM {t('forecast_event_price')} WHERE event_id = '{event_id}' "
        f"AND MOD(days_to_show, 30) = 0 ORDER BY days_to_show", project))
    lines += ["", "Full transformation SQL per stage: `docs/transformations_showcase.md`.", ""]
    return lines


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def build_report(project: str, dataset: str, skip_bronze: bool, plots: bool) -> str:
    lines = ["# Data review — bronze → gold, all sources", "",
             f"Generated {utc_now_iso()} by `eda/data_review.py` "
             f"(project `{project}`, dataset `{dataset}`). Re-run the same command "
             f"to refresh; narrative companion: `eda/data_review_2026-07.md`.", ""]
    inventory = list_bronze(project) if not skip_bronze else {}
    odata = overlap_data(project, dataset)
    pdata = price_data(project, dataset)
    imgs = render_plots(project, dataset, inventory, odata, pdata) if plots else {}

    def img(key: str, alt: str) -> list[str]:
        return [f"![{alt}]({imgs[key]})", ""] if key in imgs else []

    if not skip_bronze:
        lines += inventory_section(inventory) + [""]
        lines += img("trends_calls", "Google Trends calls per day")
        lines += samples_section(project) + [""]
        lines += fields_section(project) + [""]
    lines += coverage_section(project, dataset) + [""]
    lines += img("tm_pricing", "TM pricing coverage by day")
    lines += img("trace", "Demo event: trends signal + observed vs forecast price")
    lines += price_section(pdata) + [""]
    lines += img("prices", "Price distributions by geography, genre, show month, and observed day")
    lines += overlap_section(odata) + [""]
    lines += img("overlap", "Bay Area events per source and overlap")
    lines += img("attending", "RA attending per event")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--skip-bronze", action="store_true",
                        help="skip the GCS inventory/samples sections (BQ + CSVs only)")
    parser.add_argument("--trace", metavar="EVENT_ID", default=None,
                        help="instead of the report, trace one event bronze -> forecast")
    parser.add_argument("--plots", action="store_true",
                        help="also render eda/output/plots/review_*.png and embed them")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if args.trace:
        report = "\n".join(trace_event(args.project, args.dataset, args.trace))
        out = Path(args.output or EDA_DIR / "output" / f"trace_{args.trace}.md")
    else:
        report = build_report(args.project, args.dataset, args.skip_bronze, args.plots)
        out = Path(args.output or OUTPUT_MD)
    out.write_text(report, encoding="utf-8")
    print(f"[data_review] wrote {len(report.splitlines())} lines -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
