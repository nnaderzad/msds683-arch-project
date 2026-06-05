"""
YouTube POC — capture streaming/attention momentum signals for a hardcoded list
of touring electronic artists, as a replacement for the deprecated Spotify
popularity/followers fields.

YouTube exposes an artist's presence through (at least) two different channels:

  - OFFICIAL channel  -> the brand/marketing presence. `subscriberCount` here is
    a meaningful popularity/brand signal. `viewCount` is mostly music-video views.
  - "- Topic" channel -> an auto-generated channel that aggregates the artist's
    audio catalog ("Art Tracks"). Its `viewCount` is closer to an audio/streaming
    momentum signal, but its `subscriberCount` is near-meaningless and must NOT
    be compared against official subscriber counts.

To avoid silently mixing these signals, we resolve BOTH per artist and store them
in separate, clearly-prefixed fields. Downstream code should use:
    official_subscribers   -> popularity / brand signal
    topic_total_views      -> audio / streaming momentum signal

Auth is a simple API key for public data — no OAuth / user login required.

Run:
    python3 youtube_poc.py

The key is read from youtube_api/.env (git-ignored) or env var YOUTUBE_API_KEY.
"""

import json
import os
import sys
from pathlib import Path

import requests

API_BASE = "https://www.googleapis.com/youtube/v3"

# Edit this list — each name is resolved to channels via search.list.
ARTISTS = [
    "Daft Punk",
    "deadmau5",
    "Disclosure",
    "Flume",
    "ODESZA",
    "RÜFÜS DU SOL",
    "Bonobo",
    "Four Tet",
    "Charlotte de Witte",
    "Fred again..",
]


def load_env() -> None:
    """Load KEY=VALUE pairs from youtube_api/.env into os.environ (if present)."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def is_topic_title(title: str) -> bool:
    """A Topic channel's title ends with ' - Topic' (auto-generated)."""
    return title.strip().lower().endswith("- topic")


def search_channels(query: str, api_key: str, max_results: int = 10) -> list[dict]:
    """Return candidate channels for a query (search.list = 100 quota units).

    Each item is {'channel_id': ..., 'title': ...}.
    """
    resp = requests.get(
        f"{API_BASE}/search",
        params={
            "part": "snippet",
            "type": "channel",
            "q": query,
            "maxResults": max_results,
            "key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {"channel_id": item["id"]["channelId"], "title": item["snippet"]["title"]}
        for item in resp.json().get("items", [])
    ]


def get_channel_stats(channel_id: str, api_key: str) -> dict:
    """Fetch snippet + statistics for one channel (channels.list = 1 quota unit)."""
    resp = requests.get(
        f"{API_BASE}/channels",
        params={"part": "snippet,statistics", "id": channel_id, "key": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return items[0] if items else {}


def _int_or_none(stats: dict, key: str):
    """statistics values arrive as strings; cast to int, or None if absent."""
    return int(stats[key]) if key in stats else None


def resolve_artist(name: str, api_key: str) -> dict:
    """Resolve an artist to both their official and Topic channel (either may be
    missing) and return the flat, clearly-labeled record described in the module
    docstring."""
    candidates = search_channels(name, api_key)

    official = next((c for c in candidates if not is_topic_title(c["title"])), None)
    topic = next((c for c in candidates if is_topic_title(c["title"])), None)

    # If the Topic channel didn't surface in the generic search, try a targeted
    # search for it (extra 100 quota units, only when needed).
    if topic is None:
        topic_candidates = search_channels(f"{name} - Topic", api_key, max_results=5)
        topic = next((c for c in topic_candidates if is_topic_title(c["title"])), None)

    record = {
        "query": name,
        "channel_type": None,  # set below: "both" | "official_only" | "topic_only" | "none"
        # --- official channel (popularity / brand signal) ---
        "official_channel_id": None,
        "official_channel_title": None,
        "official_subscribers": None,
        "official_total_views": None,
        "official_video_count": None,
        # --- topic channel (audio / streaming momentum signal) ---
        "topic_channel_id": None,
        "topic_channel_title": None,
        "topic_total_views": None,
        "topic_video_count": None,
        "topic_subscribers": None,  # captured but NOT reliable — do not compare
        # --- data-quality flags ---
        "resolved_official_channel": False,
        "resolved_topic_channel": False,
        "channel_resolution_note": "",
    }

    if official is not None:
        stats = get_channel_stats(official["channel_id"], api_key).get("statistics", {})
        record["official_channel_id"] = official["channel_id"]
        record["official_channel_title"] = official["title"]
        record["official_subscribers"] = (
            None if stats.get("hiddenSubscriberCount") else _int_or_none(stats, "subscriberCount")
        )
        record["official_total_views"] = _int_or_none(stats, "viewCount")
        record["official_video_count"] = _int_or_none(stats, "videoCount")
        record["resolved_official_channel"] = True

    if topic is not None:
        stats = get_channel_stats(topic["channel_id"], api_key).get("statistics", {})
        record["topic_channel_id"] = topic["channel_id"]
        record["topic_channel_title"] = topic["title"]
        record["topic_total_views"] = _int_or_none(stats, "viewCount")
        record["topic_video_count"] = _int_or_none(stats, "videoCount")
        record["topic_subscribers"] = (
            None if stats.get("hiddenSubscriberCount") else _int_or_none(stats, "subscriberCount")
        )
        record["resolved_topic_channel"] = True

    # Summarize resolution for downstream filtering.
    if record["resolved_official_channel"] and record["resolved_topic_channel"]:
        record["channel_type"] = "both"
        record["channel_resolution_note"] = "Resolved both official and Topic channels."
    elif record["resolved_official_channel"]:
        record["channel_type"] = "official_only"
        record["channel_resolution_note"] = (
            "Only official channel found; no Topic channel matched. "
            "No audio-momentum (topic_total_views) signal available."
        )
    elif record["resolved_topic_channel"]:
        record["channel_type"] = "topic_only"
        record["channel_resolution_note"] = (
            "Only Topic channel found; no official channel matched. "
            "No reliable subscriber/popularity signal available."
        )
    else:
        record["channel_type"] = "none"
        record["channel_resolution_note"] = "No channel found for this artist name."

    return record


def main() -> int:
    load_env()
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key or "paste-your" in api_key:
        print(
            "ERROR: Missing API key. Put your key in youtube_api/.env "
            "(YOUTUBE_API_KEY=...).",
            file=sys.stderr,
        )
        return 1

    print(f"Resolving official + Topic channels for {len(ARTISTS)} artists...\n")

    results = []
    for name in ARTISTS:
        record = resolve_artist(name, api_key)
        results.append(record)

        subs = record["official_subscribers"]
        subs_str = f"{subs:,}" if subs is not None else "N/A"
        tviews = record["topic_total_views"]
        tviews_str = f"{tviews:,}" if tviews is not None else "N/A"
        print(
            f"  {name:<20} [{record['channel_type']:<13}] "
            f"official_subs={subs_str:>12}  topic_views={tviews_str:>16}"
        )

    print("\n--- Structured JSON payload ---")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
