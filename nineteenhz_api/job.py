"""Cloud Run Job entrypoint: daily 19hz listing pull + ticket-page poll.

Chains the two committed collectors as subprocesses (mirrors how
``pipeline/gold_refresh.py`` runs its steps) so the scheduled job stays a thin,
deterministic wrapper around the same scripts we run by hand:

    1. collect_19hz.py       --land-raw  -> bronze nineteenhz/dt=<utc-day>/ (HTML)
                                          + a local CSV handed to step 2
    2. poll_ticket_pages.py  --land-raw  -> bronze ticketpages/dt=<utc-day>/ (JSON-LD)

The poller only visits allowlisted domains (eventbrite/shotgun) at its polite
built-in pace (~3 s/page, ~10 min total); one pass per day. Exits non-zero on the
first failed step so the execution shows up as failed (never half-silent).

Run locally (music-demand env, repo root):

    python nineteenhz_api/job.py --csv /tmp/nineteenhz_events.csv
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def commands(csv_path: str) -> list[list[str]]:
    """The exact argv for each step, in order — pure so tests can pin it."""
    return [
        [sys.executable, str(HERE / "collect_19hz.py"),
         "--output", csv_path, "--land-raw"],
        [sys.executable, str(HERE / "poll_ticket_pages.py"),
         "--events", csv_path, "--land-raw"],
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default="/tmp/nineteenhz_events.csv",
                        help="scratch CSV that carries step 1's events to step 2")
    args = parser.parse_args()

    for argv in commands(args.csv):
        print(f"[19hz job] ▶ {' '.join(argv)}", flush=True)
        proc = subprocess.run(argv)
        if proc.returncode != 0:
            print(f"[19hz job] step failed (exit {proc.returncode}) — aborting",
                  file=sys.stderr)
            return proc.returncode
    print("[19hz job] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
