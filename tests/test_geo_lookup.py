"""Unit tests for the venue→DMA geo lookup.

Pure + offline: exercises the committed reference tables
(`google_trends_api/reference/{dma_geo,zip_to_dma}.csv`) — no network, no GCP.
"""

from __future__ import annotations

import sys
from pathlib import Path

GTRENDS = Path(__file__).resolve().parent.parent / "google_trends_api"
sys.path.insert(0, str(GTRENDS))

from geo_lookup import GeoLookup  # noqa: E402


def test_known_zips_resolve_to_expected_dma():
    g = GeoLookup()
    assert g.resolve(zip_code="94102").dma == "807"  # San Francisco
    assert g.resolve(zip_code="10001").dma == "501"  # New York
    assert g.resolve(zip_code="02116").dma == "506"  # Boston
    assert g.resolve(zip_code="89109").dma == "839"  # Las Vegas


def test_geo_code_and_method():
    hit = GeoLookup().resolve(zip_code="94704")  # Berkeley → SF DMA (a suburb)
    assert hit.geo_code == "US-CA-807"
    assert hit.dma_name.startswith("San Francisco")
    assert hit.method == "zip"


def test_zip_plus_four_and_short_zip_are_normalized():
    g = GeoLookup()
    assert g.resolve(zip_code="94102-1234").dma == "807"  # ZIP+4 → first 5
    assert g.resolve(zip_code="1001").dma is not None      # zero-pads to 01001


def test_state_fallback_when_zip_unknown():
    hit = GeoLookup().resolve(zip_code="00000", state="CA")
    assert hit is not None
    assert hit.state == "CA"
    assert hit.method == "state"


def test_unresolvable_returns_none():
    assert GeoLookup().resolve(zip_code=None, state=None) is None
