"""Unit tests for the Google Trends fetch helpers (pure, deterministic)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

GTRENDS = Path(__file__).resolve().parent.parent / "google_trends_api"
sys.path.insert(0, str(GTRENDS))

from fetch_and_land import daily_timeframe, slug, suffix_for  # noqa: E402


def test_slug_is_filesystem_safe():
    assert slug("Fred again..") == "Fred-again"
    assert slug("A$AP Rocky") == "A-AP-Rocky"
    assert slug("") == "x"


def test_suffix_for_matches_the_landed_filename_tags():
    assert suffix_for("national", "Journey") == "iot_US_Journey"
    assert suffix_for("snapshot", "Journey") == "ibr_DMA_Journey"
    assert suffix_for("hourly", "Journey") == "iot_now7d_US_Journey"
    assert suffix_for("dma", "Journey", "US-CA-807") == "iot_US-CA-807_Journey"


def test_daily_timeframe_is_a_269_day_window():
    tf = daily_timeframe(datetime(2026, 6, 14, tzinfo=timezone.utc))
    assert tf == "2025-09-18 2026-06-14"
