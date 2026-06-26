#!/usr/bin/env python3
"""Build the committed cross-source seed slice the test suite runs against (task T0).

Phase-1 silver work (A1/A2/A3) and the bronze->silver integration test (INT-1) need a
small, **real**, offline fixture covering all three sources for the same artists — so
their unit tests stay deterministic and network-free. This script carves that slice
from the landed bronze and writes it under ``tests/fixtures/seed/``.

Selection rule (deterministic, documented so a refresh diffs cleanly):
  1. Build the Ticketmaster price panel from the daily processed snapshots (reusing
     ``eda/tm_price_eda.py``) and keep events with a **complete** price history
     (present ~all of the window AND priced ~every present day — H1's ``is_complete``).
  2. Keep only artists present in **all three** sources: a YouTube channel record AND
     both Trends units (national ``iot_US`` + DMA snapshot ``ibr_DMA``). The join key
     across every source is the artist **display name** (Trends files are named with
     ``slug(name)``; YouTube records carry ``query`` = name; TM ``attraction_names`` = name).
  3. Rank artists by (has a Bay Area show, #complete events, best completeness, soonest
     show, name) and take the top ``--n-artists``, guaranteeing >=1 Bay Area show if one
     exists in the intersection. One representative complete event per artist is the seed.

For each seed artist/event it lands the real, trimmed captures across every ``dt=``
partition (so downstream price/interest **trajectory** logic has a real series):
  - ``ticketmaster/`` : the seed events' ``tm_events`` rows x snapshot day (CSV, from the
    processed parquet) + a trimmed raw bronze sample (for bronze-shape checks).
  - ``youtube/``      : each day's payload with ``records`` filtered to the seed artists.
  - ``google_trends/``: the seed artists' national + DMA-snapshot files, copied verbatim.
A ``manifest.json`` stamps provenance (as-of, dt windows, picks, counts) for diffing.

ADC-gated (reads GCS + BigQuery); run locally, NOT in CI. The committed fixtures are
what CI consumes — pure stdlib, offline. The pure selection helpers below are unit-tested
in ``tests/test_seed_fixtures.py`` without any network.

Run (repo root, ``bq``/``gsutil`` authed for the project):
    python tests/fixtures/build_seed.py
    python tests/fixtures/build_seed.py --n-artists 5 --start-date 2026-06-11
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eda"))             # _common
sys.path.insert(0, str(REPO_ROOT / "google_trends_api"))  # geo_lookup (lazy users)
sys.path.insert(0, str(REPO_ROOT))                     # common (lazy users)

from _common import DEFAULT_PROJECT, bq_rows, utc_now_iso  # noqa: E402

SEED_DIR = Path(__file__).resolve().parent / "seed"
SOURCES = ("ticketmaster", "youtube", "google_trends")

_DT_RE = re.compile(r"dt=([0-9]{4}-[0-9]{2}-[0-9]{2})")
_TRENDS_NAME_RE = re.compile(r"google_trends_(.+)_\d{8}T\d{6}Z\.json$")
_TM_STATE_RE = re.compile(r"ticketmaster_([A-Z]{2})_")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline; no network, no heavy imports)
# ---------------------------------------------------------------------------

def normalize(name: str | None) -> str:
    """Case/space-insensitive key for matching an artist across sources."""
    return " ".join(str(name or "").split()).casefold()


def trends_slug(text: str) -> str:
    """Filename-safe artist tag — mirrors ``google_trends_api/fetch_and_land.slug``."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-")[:60] or "x"


def dt_from_uri(uri: str) -> str | None:
    m = _DT_RE.search(uri)
    return m.group(1) if m else None


def cross_source_artists(
    yt_artists: set[str], national_slugs: set[str], snapshot_slugs: set[str]
) -> set[str]:
    """YouTube artists that also have BOTH Trends units (national + DMA snapshot)."""
    return {
        a for a in yt_artists
        if trends_slug(a) in national_slugs and trends_slug(a) in snapshot_slugs
    }


def match_events_to_artists(
    complete_events: list[dict], artists: set[str]
) -> dict[str, list[dict]]:
    """Map each cross-source artist -> its complete TM events (headliner match first)."""
    by_norm = {normalize(a): a for a in artists}
    matched: dict[str, list[dict]] = {}
    for ev in complete_events:
        hit = by_norm.get(normalize(ev.get("headliner")))
        if hit is None:
            for part in (ev.get("attraction_names") or "").split("|"):
                if normalize(part) in by_norm:
                    hit = by_norm[normalize(part)]
                    break
        if hit is not None:
            matched.setdefault(hit, []).append(ev)
    return matched


def _has_bay(events: list[dict]) -> bool:
    return any(e.get("is_bay_area") for e in events)


def rank_seed_artists(
    matched: dict[str, list[dict]], n: int, want_bay: bool = True
) -> list[tuple[str, list[dict]]]:
    """Deterministically order artists and take the top n, guaranteeing a Bay show."""

    def key(item: tuple[str, list[dict]]) -> tuple:
        artist, evs = item
        best = max((e.get("price_completeness") or 0.0) for e in evs)
        soonest = min((e.get("show_date") or "9999-99-99") for e in evs)
        return (not _has_bay(evs), -len(evs), -best, soonest, artist)

    ranked = sorted(matched.items(), key=key)
    picks = ranked[:n]
    if want_bay and picks and not any(_has_bay(evs) for _, evs in picks):
        bay = next((it for it in ranked if _has_bay(it[1])), None)
        if bay is not None and bay not in picks:
            picks = picks[: max(n - 1, 0)] + [bay]
    return picks


def pick_seed_event(events: list[dict]) -> dict:
    """One representative complete event for an artist (Bay first, then most complete)."""
    return sorted(
        events,
        key=lambda e: (
            not e.get("is_bay_area"),
            -(e.get("price_completeness") or 0.0),
            e.get("show_date") or "9999-99-99",
            e.get("event_id"),
        ),
    )[0]


def parse_trends_slug_sets(filenames: list[str]) -> tuple[set[str], set[str]]:
    """From Trends filenames, the slug sets that have national / DMA-snapshot units."""
    national, snapshot = set(), set()
    for name in filenames:
        m = _TRENDS_NAME_RE.search(name)
        if not m:
            continue
        suffix = m.group(1)
        if suffix.startswith("iot_US_"):
            national.add(suffix[len("iot_US_"):])
        elif suffix.startswith("ibr_DMA_"):
            snapshot.add(suffix[len("ibr_DMA_"):])
    return national, snapshot


# ---------------------------------------------------------------------------
# GCS I/O (subprocess gsutil/bq via the already-authenticated CLIs; ADC)
# ---------------------------------------------------------------------------

def gcs_ls(pattern: str) -> list[str]:
    proc = subprocess.run(["gsutil", "ls", pattern],
                          capture_output=True, text=True, check=True)
    return [u.strip() for u in proc.stdout.splitlines() if u.strip()]


def gcs_cat(uri: str) -> str:
    proc = subprocess.run(["gsutil", "cat", uri],
                          capture_output=True, text=True, check=True)
    return proc.stdout


def gcs_cat_json(uri: str):
    return json.loads(gcs_cat(uri))


# ---------------------------------------------------------------------------
# Source builders
# ---------------------------------------------------------------------------

def build_price_panel(project: str, start: str | None, end: str | None):
    """Reuse the EDA price panel: per-event completeness + geo (Bay Area) flags."""
    import tm_price_eda as eda  # heavy import kept lazy (matplotlib-free at module level)
    from geo_lookup import GeoLookup

    uris = eda.list_snapshot_uris(project, start, end)
    if not uris:
        raise SystemExit("[build_seed] no Ticketmaster snapshot parquet found")
    total_days = len({eda.dt_from_uri(u) for u in uris})
    as_of_date = max(eda.dt_from_uri(u) for u in uris)
    panel = eda.compute_metrics(eda.load_event_panel(project, uris), total_days, as_of_date)
    panel = eda.enrich_geo(panel, GeoLookup())
    return panel, uris, total_days, as_of_date


def youtube_artist_set(project: str) -> tuple[set[str], list[str]]:
    """Artists with a YouTube channel record (from the most recent daily payload)."""
    files = sorted(gcs_ls(f"gs://{project}-raw/youtube/dt=*/*.json"))
    if not files:
        return set(), files
    latest = gcs_cat_json(files[-1])
    return {r.get("query") for r in latest.get("records", []) if r.get("query")}, files


def load_tm_events_seed(project: str, uris: list[str], event_ids: list[str]) -> list[dict]:
    """The seed events' tm_events rows across every snapshot day (real price series)."""
    if not event_ids:
        return []
    in_list = ",".join("'" + e.replace("'", "") + "'" for e in event_ids)
    sql = f"""
        SELECT *,
               REGEXP_EXTRACT(_FILE_NAME, r'dt=([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})')
                 AS snapshot_date
        FROM tmhist
        WHERE event_id IN ({in_list})
        ORDER BY event_id, snapshot_date
    """
    return bq_rows(sql, project, external_table_definition="tmhist::PARQUET=" + ",".join(uris))


def fetch_raw_tm_events(project: str, seed_events: list[dict], days: list[str]) -> dict[str, dict]:
    """One real raw bronze event object per seed event (most recent day it appears)."""
    need = {e["event_id"]: e for e in seed_events}
    found: dict[str, dict] = {}
    for dt in sorted(days, reverse=True):
        if not need:
            break
        files = gcs_ls(f"gs://{project}-raw/ticketmaster/dt={dt}/*.json")
        by_state: dict[str, list[str]] = {}
        for u in files:
            m = _TM_STATE_RE.search(u)
            if m:
                by_state.setdefault(m.group(1), []).append(u)
        states = {e.get("venue_state_code") for e in need.values() if e.get("venue_state_code")}
        for st in states:
            state_files = sorted(by_state.get(st, []))
            if not state_files:
                continue
            for ev in gcs_cat_json(state_files[-1]):  # latest stamp that day
                if ev.get("id") in need:
                    found[ev["id"]] = ev
        for eid in list(found):
            need.pop(eid, None)
    return found


def materialize_youtube(project: str, seed_artists: set[str], out_dir: Path) -> dict:
    """Per-day YouTube payloads with records trimmed to the seed artists."""
    wanted = {normalize(a) for a in seed_artists}
    n_files = n_records = 0
    days: set[str] = set()
    for u in sorted(gcs_ls(f"gs://{project}-raw/youtube/dt=*/*.json")):
        dt = dt_from_uri(u)
        payload = gcs_cat_json(u)
        recs = [r for r in payload.get("records", []) if normalize(r.get("query")) in wanted]
        if not recs:
            continue
        dest = out_dir / f"youtube/dt={dt}"
        dest.mkdir(parents=True, exist_ok=True)
        trimmed = {**payload, "n_records": len(recs), "records": recs}
        (dest / u.rsplit("/", 1)[-1]).write_text(
            json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")
        n_files += 1
        n_records += len(recs)
        days.add(dt)
    return {"n_files": n_files, "n_records": n_records, "days": sorted(days)}


def materialize_trends(project: str, seed_artists: set[str], out_dir: Path) -> dict:
    """Copy the seed artists' national + DMA-snapshot Trends files verbatim (real)."""
    keep = set()
    for a in seed_artists:
        keep.add(f"iot_US_{trends_slug(a)}")
        keep.add(f"ibr_DMA_{trends_slug(a)}")
    n_national = n_snapshot = 0
    days: set[str] = set()
    for u in sorted(gcs_ls(f"gs://{project}-raw/google_trends/dt=*/*.json")):
        name = u.rsplit("/", 1)[-1]
        m = _TRENDS_NAME_RE.search(name)
        if not m or m.group(1) not in keep:
            continue
        dt = dt_from_uri(u)
        dest = out_dir / f"google_trends/dt={dt}"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_text(gcs_cat(u), encoding="utf-8")  # raw bytes, unchanged
        days.add(dt)
        if m.group(1).startswith("iot_US_"):
            n_national += 1
        else:
            n_snapshot += 1
    return {"n_national": n_national, "n_snapshot": n_snapshot, "days": sorted(days)}


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--start-date", default=None, help="earliest dt= (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="latest dt= (YYYY-MM-DD)")
    parser.add_argument("--n-artists", type=int, default=5, help="seed artist count")
    parser.add_argument("--output-dir", default=str(SEED_DIR))
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    as_of = utc_now_iso()

    # 1) Price panel -> complete events.
    panel, uris, total_days, as_of_date = build_price_panel(
        args.project, args.start_date, args.end_date)
    days = sorted({dt_from_uri(u) for u in uris})
    complete = [r for r in panel if r.get("is_complete")]
    print(f"[build_seed] panel={len(panel):,} events; complete={len(complete):,}; "
          f"{total_days} snapshot days ({days[0]}..{days[-1]})")

    # 2) Cross-source artists (present in YouTube AND both Trends units).
    yt_artists, _ = youtube_artist_set(args.project)
    trends_files = [u.rsplit("/", 1)[-1]
                    for u in gcs_ls(f"gs://{args.project}-raw/google_trends/dt=*/*.json")]
    national_slugs, snapshot_slugs = parse_trends_slug_sets(trends_files)
    xsource = cross_source_artists(yt_artists, national_slugs, snapshot_slugs)
    print(f"[build_seed] youtube_artists={len(yt_artists)}; "
          f"trends_national={len(national_slugs)} snapshot={len(snapshot_slugs)}; "
          f"cross_source={len(xsource)}")

    # 3) Match -> rank -> pick.
    matched = match_events_to_artists(complete, xsource)
    if not matched:
        print("[build_seed] no complete cross-source events found", file=sys.stderr)
        return 1
    picks = rank_seed_artists(matched, args.n_artists)
    seed_events = [pick_seed_event(evs) for _, evs in picks]
    seed_artists = {a for a, _ in picks}
    event_ids = [e["event_id"] for e in seed_events]
    if not any(e.get("is_bay_area") for e in seed_events):
        print("[build_seed] WARNING: no Bay Area show in the cross-source intersection",
              file=sys.stderr)
    for artist, ev in zip((a for a, _ in picks), seed_events):
        print(f"  - {artist}: {ev['event_name']} @ {ev.get('venue_city')}, "
              f"{ev.get('venue_state_code')} (bay={ev.get('is_bay_area')}, "
              f"completeness={ev.get('price_completeness')}, days={ev.get('days_with_price')}/"
              f"{ev.get('snapshot_days')})")

    # 4) Regenerate fixtures (clear the source subdirs we own, then rewrite).
    for src in SOURCES:
        shutil.rmtree(out_dir / src, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    tm_rows = load_tm_events_seed(args.project, uris, event_ids)
    _write_csv(out_dir / "ticketmaster" / "tm_events_seed.csv", tm_rows)
    raw_tm = fetch_raw_tm_events(args.project, seed_events, days)
    raw_dir = out_dir / "ticketmaster" / f"dt={days[-1]}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "ticketmaster_seed.json").write_text(
        json.dumps(list(raw_tm.values()), ensure_ascii=False, indent=2), encoding="utf-8")

    yt = materialize_youtube(args.project, seed_artists, out_dir)
    tr = materialize_trends(args.project, seed_artists, out_dir)

    # 5) Provenance manifest.
    import tm_price_eda as eda  # cached; read the completeness thresholds used
    manifest = {
        "as_of_ts": as_of,
        "project": args.project,
        "selection_rule": {
            "n_artists": args.n_artists,
            "complete_span": eda.COMPLETE_SPAN,
            "complete_price": eda.COMPLETE_PRICE,
            "cross_source": "youtube channel + trends national(iot_US) + snapshot(ibr_DMA)",
        },
        "tm_snapshot_window": {"start": days[0], "end": days[-1], "n_days": total_days},
        "panel": {"n_events": len(panel), "n_complete": len(complete),
                  "n_cross_source_artists": len(xsource)},
        "seed_artists": sorted(seed_artists),
        "seed_events": [
            {k: e.get(k) for k in (
                "event_id", "event_name", "headliner", "genre", "venue_name",
                "venue_city", "venue_state_code", "dma", "metro", "is_bay_area",
                "show_date", "snapshot_days", "days_with_price", "price_completeness",
                "price_min_last", "price_max_max")}
            for e in seed_events
        ],
        "counts": {
            "tm_events_rows": len(tm_rows),
            "tm_raw_events": len(raw_tm),
            "youtube_files": yt["n_files"], "youtube_records": yt["n_records"],
            "youtube_days": yt["days"],
            "trends_national_files": tr["n_national"],
            "trends_snapshot_files": tr["n_snapshot"], "trends_days": tr["days"],
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[build_seed] {len(seed_events)} artists/events; "
          f"tm_events_rows={len(tm_rows)} raw_tm={len(raw_tm)} "
          f"yt_files={yt['n_files']} trends_files={tr['n_national'] + tr['n_snapshot']} "
          f"-> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
