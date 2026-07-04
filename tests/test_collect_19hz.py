"""Offline tests for the 19hz.info listing parser (pure functions, no network)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nineteenhz_api"))

from collect_19hz import parse_artists, parse_listing, parse_price, parse_row  # noqa: E402

# A real row shape from the live page (2026-07-04), including the site's quirk of
# nesting the genre tags as an UNCLOSED <td> inside the title cell.
ROW = (
    "<td>Sat: Jul 4 <br />(3pm-10pm)"
    "<td><a href='https://www.tixr.com/groups/midwaysf/events/illum-184332'>"
    "Illum Block Party: deadmau5, Nero, Cristoph b2b Rebuke, Maneki</a>"
    " @ The Midway (San Francisco)<td>progressive house, tech house, EDM"
    "<td>$68-80 | 21+<td><td><div class='shrink'>2026/07/04</div>"
)

FREE_ROW = (
    "<td>Sat: Jul 4 <br />(11am-5pm)"
    "<td><a href='https://www.instagram.com/p/x/'>Alex Sobal, Roman A</a>"
    " @ Hedge Coffee (San Francisco)<td>house"
    "<td>free<td>Lose Yourself<td><div class='shrink'>2026/07/04</div>"
)


def test_parse_row_full_event():
    r = parse_row(ROW)
    assert r["event_date"] == "2026-07-04"
    assert r["title"] == "Illum Block Party: deadmau5, Nero, Cristoph b2b Rebuke, Maneki"
    assert r["venue"] == "The Midway" and r["city"] == "San Francisco"
    assert r["genres"] == "progressive house, tech house, EDM"
    assert r["price_min"] == 68.0 and r["price_max"] == 80.0
    assert r["age_restriction"] == "21+"
    assert r["artists"] == "deadmau5|Nero|Cristoph|Rebuke|Maneki"
    assert r["ticket_domain"] == "tixr.com"


def test_parse_row_free_event_and_headerless_rows():
    r = parse_row(FREE_ROW)
    assert r["is_free"] and r["price_min"] == 0.0
    assert r["organizers"] == "Lose Yourself"
    # header rows (no hidden date div) are dropped
    assert parse_row("<td>Date/Time<td>Event Title @ Venue<td>Tags") is None
    assert parse_listing(f"<table><tr>{ROW}</tr><tr><td>Date/Time</td></tr></table>") \
        and len(parse_listing(f"<tr>{ROW}</tr>")) == 1


def test_parse_price_variants():
    assert parse_price("$183+ | 21+") == {
        "price_text": "$183+", "age_restriction": "21+", "is_free": False,
        "price_min": 183.0, "price_max": 183.0, "price_open_ended": True}
    assert parse_price("free | All ages")["price_min"] == 0.0
    assert parse_price("")["price_min"] is None
    assert parse_price("$25.50")["price_min"] == 25.5


def test_parse_artists_lineup_conventions():
    assert parse_artists("Party: A, B b2b C") == ["A", "B", "C"]
    assert parse_artists("Colette B2B DJ Heather, Galen") == ["Colette", "DJ Heather", "Galen"]
    assert parse_artists("Solo Act") == ["Solo Act"]
    assert parse_artists("Night: X, and more") == ["X"]
    assert parse_artists("Night: X, TBA") == ["X"]
