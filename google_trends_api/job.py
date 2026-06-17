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
  JOB_MODE=backfill|daily   TOP_N=250   MAX_UNITS=0(all)   TRENDS_SLEEP=20
  STATES=ALL|CA,NY,...      CLOUD_RUN_TASK_INDEX=0   CLOUD_RUN_TASK_COUNT=1
  DAILY_CALL_BUDGET=0(off)  RUN_DEADLINE_SECONDS=0(off)  RESUME_LOOKBACK_DAYS=0
  GCS_RAW_BUCKET (via common/gcs_io)

Stays under the Trends rate limit deterministically: run as ONE stream (no parallel
shards) so TRENDS_SLEEP is the global min interval between calls, and stop gracefully
(exit 0) at whichever of three guards trips first — DAILY_CALL_BUDGET (global calls
per UTC day, counted from the GCS partition), RUN_DEADLINE_SECONDS (wall-clock), or
queue exhaustion. RESUME_LOOKBACK_DAYS skips units already landed in recent days so a
budget-capped pass advances over days instead of redoing the soonest-first head.

Run locally (bounded smoke):
  JOB_MODE=daily MAX_UNITS=4 STATES=CA python google_trends_api/job.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
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


def landed_state(bucket: str, lookback_days: int = 0) -> tuple[set[str], int]:
    """Scan recent dt= partitions once for (resume done-set, today's call count).

    Returns:
        done_suffixes: suffixes present in TODAY plus the previous ``lookback_days``
            partitions. A unit already landed in this window is skipped — cross-day
            resume so a budget-capped pass advances over days instead of redoing the
            soonest-first head every UTC day.
        calls_today: number of landed files in TODAY's partition only — the
            deterministic, GLOBAL (every job writes here) per-UTC-day call ledger
            used to enforce DAILY_CALL_BUDGET.
    """
    client = storage.Client(project=gcs_io.PROJECT_ID)
    today = datetime.now(timezone.utc).date()
    done: set[str] = set()
    calls_today = 0
    for delta in range(max(lookback_days, 0) + 1):
        dt = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
        n = 0
        for blob in client.list_blobs(bucket, prefix=f"google_trends/dt={dt}/"):
            n += 1
            if (m := _STAMP.search(blob.name)):
                done.add(m.group(1))
        if delta == 0:
            calls_today = n
    return done, calls_today


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
    min_interval = float(env("TRENDS_SLEEP", "20"))      # global min seconds between calls
    daily_budget = int(env("DAILY_CALL_BUDGET", "0"))    # global calls/UTC-day; 0 = off
    run_deadline = float(env("RUN_DEADLINE_SECONDS", "0"))  # wall-clock cap; 0 = off
    lookback_days = int(env("RESUME_LOOKBACK_DAYS", "0"))   # cross-day resume window
    states_raw = env("STATES", "ALL")
    states = None if states_raw.upper() in ("", "ALL") else [s.strip().upper() for s in states_raw.split(",")]
    task_index = int(env("CLOUD_RUN_TASK_INDEX", "0"))
    task_count = int(env("CLOUD_RUN_TASK_COUNT", "1"))
    # Backfill cheap-first: INCLUDE_DMA=false does national + DMA snapshot only;
    # true adds the deep per-DMA daily series (the expensive pass).
    include_dma = env("INCLUDE_DMA", "true").lower() in ("1", "true", "yes")

    queue = build_queue(mode, top_n, states, include_dma)
    shard = queue[task_index::task_count]  # disjoint round-robin slice per task

    done, calls_today_start = landed_state(gcs_io.DEFAULT_RAW_BUCKET, lookback_days)
    pending = [u for u in shard if suffix_for(u["kind"], u["artist"], u["geo_code"]) not in done]
    if max_units > 0:
        pending = pending[:max_units]

    logger.info(
        "mode=%s task=%d/%d queue=%d shard=%d done(lookback=%dd)=%d pending=%d "
        "calls_today=%d budget=%d interval=%.0fs deadline=%.0fs",
        mode, task_index, task_count, len(queue), len(shard), lookback_days, len(done),
        len(pending), calls_today_start, daily_budget, min_interval, run_deadline,
    )

    client = TrendsClient(min_interval=min_interval)
    tf_daily = daily_timeframe()
    started = time.monotonic()
    landed, failed = 0, {}
    stop_reason = "queue_done"
    for u in pending:
        # Deterministic stop guards — whichever trips first ends the run GRACEFULLY
        # (summary + exit 0), never a platform SIGKILL. calls_made counts attempts
        # (incl. retries), so the budget check is conservative re: real API hits.
        if daily_budget > 0 and calls_today_start + client.calls_made >= daily_budget:
            stop_reason = "daily_budget"
            break
        if run_deadline > 0 and time.monotonic() - started >= run_deadline:
            stop_reason = "time_budget"
            break
        try:
            run_unit(client, u["kind"], u["artist"], u["query"],
                     geo_code=u["geo_code"], tf_daily=tf_daily, dry_run=False)
            landed += 1
        except Exception as exc:  # isolate per-unit failures; keep going
            suffix = suffix_for(u["kind"], u["artist"], u["geo_code"])
            failed[suffix] = f"{type(exc).__name__}: {exc}"
            logger.warning("unit failed: %s -> %s", suffix, failed[suffix])

    elapsed_min = (time.monotonic() - started) / 60.0
    summary = {
        "run_ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode, "task": f"{task_index}/{task_count}",
        "queue": len(queue), "shard": len(shard), "pending": len(pending),
        "stop_reason": stop_reason,
        "landed": landed, "failed_count": len(failed), "failed": failed,
        "calls_this_run": client.calls_made,
        "calls_today_start": calls_today_start,
        "calls_today_total": calls_today_start + client.calls_made,
        "daily_budget": daily_budget,
        "min_interval_s": min_interval,
        "achieved_rate_per_min": round(client.calls_made / elapsed_min, 2) if elapsed_min > 0 else None,
    }
    print(json.dumps(summary))  # -> Cloud Logging run history
    if failed:
        print(json.dumps(summary), file=sys.stderr)  # severity ERROR -> alert policy
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
