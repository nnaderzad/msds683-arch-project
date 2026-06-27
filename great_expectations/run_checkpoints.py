#!/usr/bin/env python3
"""Run (or list) Great Expectations checkpoints — the 1.x replacement for the
removed ``great_expectations checkpoint ...`` CLI.

Usage (from the repo root):
    python great_expectations/run_checkpoints.py            # run all offline checkpoints
    python great_expectations/run_checkpoints.py --list     # list checkpoints in the project

Runs, offline against the seed fixtures (no creds, no network):
  * the C1 scaffold smoke checkpoint,
  * the C2 bronze raw-JSON landing checks (one per source),
  * the C3 silver schema / null / value-range / join-key checks (one per table), and
  * the C4 gold fact_event_demand integrity check.

Exit code is non-zero if any validation fails, so this doubles as a CI / pre-load gate.
"""

from __future__ import annotations

import argparse

import bronze_suites
import gold_suites
import gx_project
import silver_suites


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="list checkpoints in the project and exit"
    )
    args = parser.parse_args(argv)

    if args.list:
        names = (
            [gx_project.SMOKE_CHECKPOINT]
            + [f"{key}_checkpoint" for key in bronze_suites.BRONZE_SOURCES]
            + [f"silver_{table}_checkpoint" for table in silver_suites.SILVER_TABLES]
            + [f"gold_{table}_checkpoint" for table in gold_suites.GOLD_TABLES]
        )
        for name in names:
            print(name)
        return 0

    ok = True

    smoke = gx_project.run_offline()
    print(f"checkpoint {gx_project.SMOKE_CHECKPOINT}: {'PASSED' if smoke.success else 'FAILED'}")
    ok &= smoke.success

    for key, result in bronze_suites.run_bronze_offline().items():
        print(f"checkpoint {key}_checkpoint: {'PASSED' if result.success else 'FAILED'}")
        ok &= result.success

    for table, result in silver_suites.run_silver_offline().items():
        print(f"checkpoint silver_{table}_checkpoint: {'PASSED' if result.success else 'FAILED'}")
        ok &= result.success

    for table, result in gold_suites.run_gold_offline().items():
        print(f"checkpoint gold_{table}_checkpoint: {'PASSED' if result.success else 'FAILED'}")
        ok &= result.success

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
