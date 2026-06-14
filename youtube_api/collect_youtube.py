#!/usr/bin/env python3
"""Collect YouTube popularity stats for the roster and land them in bronze.

YouTube is the project's GLOBAL popularity/momentum signal (the Data API exposes
no geography and no history), so we snapshot it forward, daily. For each roster
artist we capture, per the official-vs-"- Topic" channel rationale in youtube_poc.py:
    official_subscribers / official_total_views / official_video_count  -> brand/popularity
    topic_total_views / topic_video_count                                -> audio/streaming momentum

Quota-aware (YouTube Data API = 10,000 units/day):
    search.list   = 100 units  -> resolving an artist's channels is EXPENSIVE
    channels.list = 1 unit      -> fetching stats is CHEAP
So channel IDs are resolved ONCE and cached in GCS, capping the search spend per
run (RESOLVE_MAX_UNITS). Every run then refreshes stats (cheap) for all resolved
artists and lands one daily snapshot. The roster comes from the same Ticketmaster
silver table the Trends pipeline uses, so all sources cover the same acts.

Bronze: gs://<raw>/youtube/dt=<date>/youtube_<stamp>.json   (one record per artist)
Cache:  gs://<processed>/youtube/channel_cache.json

Run locally (needs YOUTUBE_API_KEY in env or youtube_api/.env):
    python youtube_api/collect_youtube.py --top-n 50 --resolve-max-units 4000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Imports: this module's dir (youtube_poc), the repo root (common), and the
# Trends package (build_roster/geo_lookup/config) all need to be importable.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))                       # repo root -> common
sys.path.insert(0, str(_HERE.parents[1] / "google_trends_api"))  # build_roster, geo_lookup

import requests  # noqa: E402

from google.cloud import storage  # noqa: E402

from common import gcs_io  # noqa: E402
from build_roster import build_frames, fetch_upcoming  # noqa: E402
from geo_lookup import GeoLookup  # noqa: E402
from youtube_poc import (  # noqa: E402
    _int_or_none, get_channel_stats, is_topic_title, load_env, search_channels,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("collect_youtube")

SOURCE = "youtube"
SEARCH_UNITS = 100   # search.list quota cost
CHANNELS_UNITS = 1   # channels.list quota cost
PROJECT_ID = gcs_io.PROJECT_ID
PROCESSED_BUCKET = os.environ.get("GCS_PROCESSED_BUCKET", f"{PROJECT_ID}-processed")
CACHE_BLOB = "youtube/channel_cache.json"
WATCHLIST_PATH = Path(__file__).with_name("watchlist.csv")


# --- GCS-backed channel-id cache --------------------------------------------

def _cache_blob() -> storage.Blob:
    return storage.Client(project=PROJECT_ID).bucket(PROCESSED_BUCKET).blob(CACHE_BLOB)


def load_cache() -> dict[str, dict]:
    blob = _cache_blob()
    return json.loads(blob.download_as_text()) if blob.exists() else {}


def save_cache(cache: dict[str, dict]) -> None:
    _cache_blob().upload_from_string(
        json.dumps(cache, ensure_ascii=False, indent=2), content_type="application/json"
    )


# --- Resolution (expensive, cached) -----------------------------------------

def resolve_channel_ids(name: str, api_key: str) -> tuple[dict, int]:
    """Resolve an artist to official + Topic channel ids; return (entry, units_used)."""

    units = SEARCH_UNITS
    candidates = search_channels(name, api_key)
    official = next((c for c in candidates if not is_topic_title(c["title"])), None)
    topic = next((c for c in candidates if is_topic_title(c["title"])), None)
    if topic is None:  # one targeted retry for the Topic channel
        topic_candidates = search_channels(f"{name} - Topic", api_key, max_results=5)
        units += SEARCH_UNITS
        topic = next((c for c in topic_candidates if is_topic_title(c["title"])), None)
    return (
        {
            "official_id": official["channel_id"] if official else None,
            "official_title": official["title"] if official else None,
            "topic_id": topic["channel_id"] if topic else None,
            "topic_title": topic["title"] if topic else None,
            "resolved_ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        units,
    )


# --- Stats (cheap, every run) -----------------------------------------------

def stats_record(name: str, entry: dict, api_key: str) -> tuple[dict, int]:
    """Fetch current stats for an artist's cached channels; return (record, units)."""

    rec = {
        "query": name,
        "official_channel_id": entry.get("official_id"),
        "official_channel_title": entry.get("official_title"),
        "official_subscribers": None,
        "official_total_views": None,
        "official_video_count": None,
        "topic_channel_id": entry.get("topic_id"),
        "topic_channel_title": entry.get("topic_title"),
        "topic_total_views": None,
        "topic_video_count": None,
    }
    units = 0
    if entry.get("official_id"):
        s = get_channel_stats(entry["official_id"], api_key).get("statistics", {})
        units += CHANNELS_UNITS
        rec["official_subscribers"] = None if s.get("hiddenSubscriberCount") else _int_or_none(s, "subscriberCount")
        rec["official_total_views"] = _int_or_none(s, "viewCount")
        rec["official_video_count"] = _int_or_none(s, "videoCount")
    if entry.get("topic_id"):
        s = get_channel_stats(entry["topic_id"], api_key).get("statistics", {})
        units += CHANNELS_UNITS
        rec["topic_total_views"] = _int_or_none(s, "viewCount")
        rec["topic_video_count"] = _int_or_none(s, "videoCount")
    return rec, units


# --- Roster ------------------------------------------------------------------

def roster_artists(top_n: int, states: list[str] | None) -> list[str]:
    """Selected touring artists (display names) from the Ticketmaster silver table."""
    rows = fetch_upcoming(states)
    artist_df, _, stats = build_frames(rows, GeoLookup(), top_n)
    logger.info("Roster: %s", stats)
    return list(artist_df.loc[artist_df["selected"], "artist"])


def load_watchlist() -> list[str]:
    """Popular-artist watchlist (forward-collected beyond the TM roster)."""
    if not WATCHLIST_PATH.exists():
        return []
    names = []
    for line in WATCHLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line.lower() != "artist":
            names.append(line)
    return names


def build_universe(top_n: int, states: list[str] | None, max_artists: int) -> list[str]:
    """TM roster ∪ watchlist (roster first), de-duplicated and capped for quota.

    Daily stats cost ~2 units/artist, so the cap keeps a full run under the
    10,000-unit/day YouTube quota (with headroom for incremental resolution).
    """
    seen: set[str] = set()
    universe: list[str] = []
    for name in roster_artists(top_n, states) + load_watchlist():
        disp = " ".join(str(name).split())
        key = disp.lower()
        if key and key not in seen:
            seen.add(key)
            universe.append(disp)
    return universe[:max_artists] if max_artists > 0 else universe


def get_api_key() -> str:
    load_env()  # youtube_api/.env if present
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key or "paste-your" in key:
        raise SystemExit("Missing YOUTUBE_API_KEY (env or youtube_api/.env).")
    return key


def main() -> int:
    # Defaults come from env (so the Cloud Run job is configured via env vars) but
    # stay CLI-overridable for local runs.
    states_env = os.environ.get("STATES", "").strip()
    default_states = (
        None if states_env.upper() in ("", "ALL")
        else [s.strip().upper() for s in states_env.split(",") if s.strip()]
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=int(os.environ.get("TOP_N", "250")))
    parser.add_argument("--states", nargs="*", default=default_states, help="Limit roster (smoke).")
    parser.add_argument("--resolve-max-units", type=int,
                        default=int(os.environ.get("RESOLVE_MAX_UNITS", "8000")),
                        help="Quota budget for channel resolution per run (search=100 each).")
    parser.add_argument("--max-artists", type=int,
                        default=int(os.environ.get("YOUTUBE_MAX_ARTISTS", "2000")),
                        help="Cap on the artist universe (daily stats ~2 units each; quota=10k/day).")
    parser.add_argument("--dry-run", action="store_true", help="Don't land/cache to GCS.")
    args = parser.parse_args()

    api_key = get_api_key()
    names = build_universe(args.top_n, args.states, args.max_artists)
    logger.info("Artist universe: %d (TM roster + watchlist, cap %d)", len(names), args.max_artists)
    cache = {} if args.dry_run else load_cache()

    # 1) Resolve channels for not-yet-cached artists, capped by quota budget.
    resolve_units, newly_resolved = 0, 0
    for name in names:
        if name in cache:
            continue
        if resolve_units + SEARCH_UNITS > args.resolve_max_units:
            break
        try:
            entry, used = resolve_channel_ids(name, api_key)
        except requests.HTTPError as exc:  # likely daily quota exhausted — stop gracefully
            logger.warning("Channel resolution stopped (quota/HTTP): %s", exc)
            break
        resolve_units += used
        cache[name] = entry
        newly_resolved += 1
    if not args.dry_run and newly_resolved:
        save_cache(cache)

    # 2) Refresh stats (cheap) for every cached roster artist + land a snapshot.
    records, stats_units = [], 0
    for name in names:
        entry = cache.get(name)
        if not entry or not (entry.get("official_id") or entry.get("topic_id")):
            continue
        try:
            rec, used = stats_record(name, entry, api_key)
        except requests.HTTPError as exc:
            logger.warning("Stats fetch failed for %s: %s", name, exc)
            continue
        stats_units += used
        records.append(rec)

    payload = {
        "source": SOURCE,
        "extract_ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_records": len(records),
        "records": records,
    }
    uri = None
    if not args.dry_run and records:
        uri = gcs_io.upload_raw(SOURCE, payload)

    logger.info(
        "Done: roster=%d resolved_now=%d (%d units) stats=%d (%d units) landed=%s",
        len(names), newly_resolved, resolve_units, len(records), stats_units, uri or "(dry-run)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
