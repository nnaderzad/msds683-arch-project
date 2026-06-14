#!/usr/bin/env python3
"""Cloud Run Job entrypoint — Google Trends backfill / daily collector.

Modes (env ``JOB_MODE``):
  backfill   national + DMA-snapshot per artist, plus per-DMA daily series for the
             metros each artist actually plays (the deep, expensive pass).
  daily      national + DMA-snapshot refresh (daily resolution) for the roster.

Built to run as a sharded Cloud Run Job: each task (CLOUD_RUN_TASK_INDEX of
CLOUD_RUN_TASK_COUNT) processes a disjoint slice of the soonest-show-first queue.
Idempotent: units already landed in today's ``dt=`` partition are skipped, so a
crashed/retried/duplicated task resumes instead of re-spending Trends calls.

The roster is regenerated fresh from the Ticketmaster silver table each run, so new
upcoming shows are picked up automatically.

Env (all optional; sensible defaults):
  JOB_MODE=backfill|daily   TOP_N=250   MAX_UNITS=0(all)   TRENDS_SLEEP=12
  STATES=ALL|CA,NY,...      CLOUD_RUN_TASK_INDEX=0   CLOUD_RUN_TASK_COUNT=1
  GCS_RAW_BUCKET (via common/gcs_io)

Run locally (bounded smoke):
  JOB_MODE=daily MAX_UNITS=4 STATES=CA python google_trends_api/job.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root for `common`
from common import gcs_io  # noqa: E402
from google.cloud import storage  # noqa: E402

from build_roster import build_frames, fetch_upcoming  # noqa: E402
from fetch_and_land import daily_timeframe, run_unit, suffix_for  # noqa: E402
from geo_lookup import GeoLookup  # noqa: E402
from trends_client import TrendsClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gtrends_job")

_STAMP = re.compile(r"google_trends_(.+)_\d{8}T\d{6}Z\.json$")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def landed_suffixes_today(bucket: str) -> set[str]:
    """Suffixes already present in today's dt= partition (for resume/idempotency)."""
    dt = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    client = storage.Client(project=gcs_io.PROJECT_ID)
    out: set[str] = set()
    for blob in client.list_blobs(bucket, prefix=f"google_trends/dt={dt}/"):
        if (m := _STAMP.search(blob.name)):
            out.add(m.group(1))
    return out


def build_queue(
    mode: str, top_n: int, states: list[str] | None, include_dma: bool = True
) -> list[dict]:
    """Soonest-show-first list of work units, regenerated from the TM silver table."""

    rows = fetch_upcoming(states)
    artist_df, targets_df, stats = build_frames(rows, GeoLookup(), top_n)
    logger.info("Roster: %s", stats)
    selected = artist_df[artist_df["selected"]]
    soonest = dict(zip(selected["artist"], selected["soonest_date"].astype(str)))
    query_of = dict(zip(selected["artist"], selected["query"]))

    # Daily resolution is enough for demand modeling, so both backfill and daily
    # collect: national daily series + the all-DMA geographic snapshot per artist.
    units: list[dict] = []
    for artist, sdate in soonest.items():
        query = query_of[artist]
        units.append({"kind": "national", "artist": artist, "query": query,
                      "geo_code": None, "sort": sdate})
        units.append({"kind": "snapshot", "artist": artist, "query": query,
                      "geo_code": None, "sort": sdate})

    if mode == "backfill" and include_dma:
        tgt = targets_df[targets_df["artist"].isin(soonest)]
        for t in tgt.itertuples():
            units.append({"kind": "dma", "artist": t.artist, "query": t.query,
                          "geo_code": t.geo_code, "sort": str(t.soonest_in_dma)})

    units.sort(key=lambda u: (u["sort"], u["artist"], u["kind"]))
    return units


def main() -> int:
    mode = env("JOB_MODE", "backfill")
    top_n = int(env("TOP_N", "250"))
    max_units = int(env("MAX_UNITS", "0"))
    sleep = float(env("TRENDS_SLEEP", "12"))
    states_raw = env("STATES", "ALL")
    states = None if states_raw.upper() in ("", "ALL") else [s.strip().upper() for s in states_raw.split(",")]
    task_index = int(env("CLOUD_RUN_TASK_INDEX", "0"))
    task_count = int(env("CLOUD_RUN_TASK_COUNT", "1"))
    # Backfill cheap-first: INCLUDE_DMA=false does national + DMA snapshot only;
    # true adds the deep per-DMA daily series (the expensive pass).
    include_dma = env("INCLUDE_DMA", "true").lower() in ("1", "true", "yes")

    queue = build_queue(mode, top_n, states, include_dma)
    shard = queue[task_index::task_count]  # disjoint round-robin slice per task

    done = landed_suffixes_today(gcs_io.DEFAULT_RAW_BUCKET)
    pending = [u for u in shard if suffix_for(u["kind"], u["artist"], u["geo_code"]) not in done]
    if max_units > 0:
        pending = pending[:max_units]

    logger.info(
        "mode=%s task=%d/%d queue=%d shard=%d already_done=%d pending=%d sleep=%.0fs",
        mode, task_index, task_count, len(queue), len(shard), len(done), len(pending), sleep,
    )

    client = TrendsClient(request_sleep=sleep)
    tf_daily = daily_timeframe()
    landed, failed = 0, {}
    for i, u in enumerate(pending, 1):
        try:
            run_unit(client, u["kind"], u["artist"], u["query"],
                     geo_code=u["geo_code"], tf_daily=tf_daily, dry_run=False)
            landed += 1
        except Exception as exc:  # isolate per-unit failures; keep going
            failed[suffix_for(u["kind"], u["artist"], u["geo_code"])] = f"{type(exc).__name__}: {exc}"
            logger.warning("unit failed: %s", failed)
        if i < len(pending):
            client.sleep_between_requests()

    summary = {
        "run_ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode, "task": f"{task_index}/{task_count}",
        "queue": len(queue), "shard": len(shard),
        "landed": landed, "failed_count": len(failed), "failed": failed,
    }
    print(json.dumps(summary))  # -> Cloud Logging run history
    if failed:
        print(json.dumps(summary), file=sys.stderr)  # severity ERROR -> alert policy
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
