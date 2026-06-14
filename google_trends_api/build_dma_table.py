#!/usr/bin/env python3
"""Generate the canonical Google Trends DMA reference table (committed artifact).

Google Trends addresses metros by Nielsen DMA using a geo code of the form
``US-<STATE>-<DMA>`` (e.g. ``US-CA-807``); ``interest_over_time`` needs exactly
that form (bare ``US-<DMA>`` returns HTTP 400). This one-time generator pulls the
full DMA list straight from Trends itself — a single
``interest_by_region(resolution="DMA", inc_geo_code=True)`` call returns every
DMA's name + geoCode — then derives the ``US-<STATE>-<DMA>`` code by parsing the
primary state out of each DMA name (Google's canonical prefix is the FIRST state
listed, e.g. "Boston (Manchester) MA-NH" -> MA).

Output: ``reference/dma_geo.csv`` (committed), so downstream code reads a static,
reproducible table without re-hitting the rate-limited endpoint. Re-run only to
refresh. Derived codes are validated against the hand-verified entries in
``config.US_METROS``; mismatches are logged.

Run (from repo root):
    python google_trends_api/build_dma_table.py
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

import config
from trends_client import TrendsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_dma_table")

OUTPUT = Path(__file__).with_name("reference") / "dma_geo.csv"
SEED_QUERY = "concert"  # broad term, so nearly every DMA returns a value + code

# Valid USPS codes (50 states + DC), so we don't mistake a 2-letter city
# fragment for a state when scanning a DMA name.
_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
_TWO_LETTER = re.compile(r"\b([A-Z]{2})\b")


def primary_state(dma_name: str) -> str | None:
    """Google's canonical prefix is the FIRST state listed in the DMA name.

    e.g. "Boston MA-Manchester NH" -> MA, "Portland-Auburn ME" -> ME. We take the
    first 2-letter token that is an actual USPS state code (a city fragment like
    "Ft" never matches because of the all-caps + valid-state check).
    """
    for token in _TWO_LETTER.findall(dma_name):
        if token in _US_STATES:
            return token
    return None


def build_table(seed_query: str = SEED_QUERY) -> pd.DataFrame:
    """One interest_by_region call -> tidy (dma, dma_name, state, geo_code) table."""

    client = TrendsClient(request_sleep=0)
    frame = client.interest_by_region(seed_query, geo="US", resolution="DMA")
    if "geoCode" not in frame.columns:
        raise SystemExit(f"Unexpected interest_by_region columns: {list(frame.columns)}")

    rows: list[dict[str, str | None]] = []
    for dma_name, geo_code in frame["geoCode"].items():
        raw = str(geo_code).strip()
        if raw.upper().startswith("US-"):  # Trends already gave the full code
            parts = raw.upper().split("-")
            geo = raw.upper()
            dma_num = parts[-1]
            state = parts[1] if len(parts) == 3 else None
        else:  # Trends gave the bare DMA number — derive the state prefix
            dma_num = re.sub(r"\D", "", raw)
            state = primary_state(str(dma_name))
            geo = f"US-{state}-{dma_num}" if state and dma_num else None
        rows.append(
            {"dma": dma_num, "dma_name": str(dma_name), "state": state, "geo_code": geo}
        )

    return (
        pd.DataFrame(rows)
        .drop_duplicates(subset="dma")
        .sort_values("dma")
        .reset_index(drop=True)
    )


def validate(df: pd.DataFrame) -> int:
    """Check derived geo_codes against config.US_METROS; return mismatch count."""

    by_dma = {str(r.dma): r.geo_code for r in df.itertuples()}
    problems = [
        f"  DMA {m.dma} ({m.name}): expected {m.geo}, got {by_dma.get(str(m.dma))}"
        for m in config.US_METROS
        if by_dma.get(str(m.dma)) != m.geo
    ]
    if problems:
        logger.warning("Geo-code mismatches vs config.US_METROS:\n%s", "\n".join(problems))
    else:
        logger.info("All %d config.US_METROS codes match.", len(config.US_METROS))
    return len(problems)


def main() -> int:
    df = build_table()
    missing = int(df["geo_code"].isna().sum())
    logger.info("Built %d DMAs (%d missing a parsed state prefix).", len(df), missing)
    validate(df)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT, index=False)
    logger.info("Wrote %s", OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
