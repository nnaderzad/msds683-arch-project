#!/usr/bin/env python3
"""Generate the committed ZIP -> DMA crosswalk used to map venues to metros.

Ticketmaster gives every event a venue ZIP (~100% coverage); Google Trends works
in Nielsen DMAs. This crosswalk bridges them so a venue resolves to the DMA whose
search-interest series we pull.

Source: a public gist by `clarkenheim` — a ZIP->DMA TSV built by taking each ZIP's
geographic centroid and finding the Nielsen DMA polygon that contains it (method
documented in the gist). Nielsen DMA boundaries are Nielsen IP; this derived
crosswalk is used here for non-commercial academic work, with attribution:
  https://gist.github.com/clarkenheim/023882f8d77741f4d5347f80d95bc259

We download it, keep only (zip, dma), validate every DMA against
``reference/dma_geo.csv`` (from build_dma_table.py), and write
``reference/zip_to_dma.csv``. The cleaned CSV is committed so runtime never depends
on the gist staying up — re-run this only to refresh. Verified mappings:
94102->807 (SF), 10001->501 (NYC), 02116->506 (Boston), 89109->839 (Las Vegas).

Run (from repo root):
    python google_trends_api/build_zip_dma_table.py
    python google_trends_api/build_zip_dma_table.py --source /path/to/local.tsv
"""

from __future__ import annotations

import argparse
import logging
import urllib.request
from io import StringIO
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_zip_dma_table")

SOURCE_URL = (
    "https://gist.githubusercontent.com/clarkenheim/"
    "023882f8d77741f4d5347f80d95bc259/raw/"
    "c4025b639dc0c83767dfb04e6c6f1c30990179e7/Zip%20Codes%20to%20DMAs"
)
REFERENCE = Path(__file__).with_name("reference")
DMA_GEO = REFERENCE / "dma_geo.csv"
OUTPUT = REFERENCE / "zip_to_dma.csv"


def load_source(source: str) -> pd.DataFrame:
    """Read the raw ZIP->DMA TSV (no header): columns zip, dma, dma_name."""

    if source.startswith(("http://", "https://")):
        logger.info("Downloading crosswalk from %s", source)
        with urllib.request.urlopen(source, timeout=60) as resp:
            handle: object = StringIO(resp.read().decode("utf-8"))
    else:
        handle = source
    return pd.read_csv(
        handle,
        sep="\t",
        header=None,
        names=["zip", "dma", "dma_name"],
        dtype={"zip": str, "dma": str, "dma_name": str},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE_URL, help="URL or local TSV path.")
    args = parser.parse_args()

    df = load_source(args.source)
    # Normalize: 5-digit zero-padded ZIP, trimmed DMA; one row per ZIP.
    df["zip"] = df["zip"].str.strip().str.zfill(5)
    df["dma"] = df["dma"].str.strip()
    df = df.dropna(subset=["zip", "dma"]).drop_duplicates(subset="zip")

    valid_dmas = set(pd.read_csv(DMA_GEO, dtype={"dma": str})["dma"])
    in_ref = df["dma"].isin(valid_dmas)
    if (n_bad := int((~in_ref).sum())):
        bad = sorted(set(df.loc[~in_ref, "dma"]))
        logger.warning(
            "Dropping %d ZIPs whose DMA isn't in dma_geo.csv (%d codes: %s)",
            n_bad, len(bad), bad[:20],
        )

    clean = df.loc[in_ref, ["zip", "dma"]].sort_values("zip").reset_index(drop=True)
    logger.info(
        "Writing %d ZIPs covering %d/%d DMAs",
        len(clean), clean["dma"].nunique(), len(valid_dmas),
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(OUTPUT, index=False)
    logger.info("Wrote %s", OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
