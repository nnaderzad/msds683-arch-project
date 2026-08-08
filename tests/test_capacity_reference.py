"""Tests for the curated venue-capacity reference feeding dim_venue.capacity."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "silver"))

bd = importlib.import_module("build_dimensions")


class _NoGeo:
    def resolve(self, zip_code=None, state=None):
        return None


def test_load_capacity_reference_skips_unknowns(tmp_path):
    csv_path = tmp_path / "caps.csv"
    csv_path.write_text(
        "venue_name,city,state_code,capacity,capacity_type,source_url,notes\n"
        "Public Works,San Francisco,CA,750,stated,https://example.com,main room\n"
        "Mystery Hall,Oakland,CA,,unknown,,no source found\n"
        "The Fillmore,San Francisco,ca,1150,stated,https://example.com,\n",
        encoding="utf-8",
    )
    ref = bd.load_capacity_reference(csv_path)
    assert ref[("public works", "CA")] == 750
    assert ref[("the fillmore", "CA")] == 1150
    assert ("mystery hall", "CA") not in ref


def test_load_capacity_reference_missing_file_is_empty(tmp_path):
    assert bd.load_capacity_reference(tmp_path / "nope.csv") == {}


def test_build_dim_venue_fills_capacity_on_name_state_match():
    tm_rows = [
        {"venue_id": "tmv1", "venue_name": "Public  Works", "venue_city": "San Francisco",
         "venue_state_code": "CA", "venue_postal_code": None, "venue_address": None,
         "venue_latitude": None, "venue_longitude": None},
        {"venue_id": "tmv2", "venue_name": "Unmatched Room", "venue_city": "Reno",
         "venue_state_code": "NV", "venue_postal_code": None, "venue_address": None,
         "venue_latitude": None, "venue_longitude": None},
    ]
    ref = {("public works", "CA"): 750}
    venues = {v["ticketmaster_venue_id"]: v for v in bd.build_dim_venue(tm_rows, _NoGeo(), ref)}
    assert venues["tmv1"]["capacity"] == 750  # whitespace-normalized name matched
    assert venues["tmv2"]["capacity"] is None  # no guess for unmatched venues


def test_committed_reference_is_loadable_and_substantial():
    ref = bd.load_capacity_reference()
    assert len(ref) >= 150  # 197 researched capacities committed on 2026-08-08
    assert ref[("public works", "CA")] == 750
