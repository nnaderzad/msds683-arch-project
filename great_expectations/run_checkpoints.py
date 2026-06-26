#!/usr/bin/env python3
"""Run (or list) Great Expectations checkpoints — the 1.x replacement for the
removed ``great_expectations checkpoint ...`` CLI.

Usage (from the repo root):
    python great_expectations/run_checkpoints.py            # run the offline smoke checkpoint
    python great_expectations/run_checkpoints.py --list     # list checkpoints in the project

Exit code is non-zero if any validation fails, so this doubles as a CI / pre-load
gate. The offline path uses only the committed seed fixtures — no creds, no network.
"""

from __future__ import annotations

import argparse

import gx_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="list checkpoints in the project and exit"
    )
    args = parser.parse_args(argv)

    if args.list:
        context = gx_project.get_context()
        # Ensure the scaffold checkpoint exists before listing (idempotent).
        batch = gx_project.register_seed_datasource(context)
        suite = gx_project.build_smoke_suite(context)
        gx_project.build_smoke_checkpoint(context, batch, suite)
        for name in gx_project.list_checkpoints(context):
            print(name)
        return 0

    result = gx_project.run_offline()
    status = "PASSED" if result.success else "FAILED"
    print(f"checkpoint {gx_project.SMOKE_CHECKPOINT}: {status}")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
