"""Offline tests for the scene bronze -> silver transforms (pure, no network/BQ)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "pipeline" / "silver"))

scene = importlib.import_module("scene_to_silver")

# Real row shape from the live 19hz page (see tests/test_collect_19hz.py).
HTML = (
    "<table><tr><td>Sat: Jul 4 <br />(3pm-10pm)"
    "<td><a href='https://www.tixr.com/groups/midwaysf/events/illum-184332'>"
    "Illum Block Party: deadmau5, Nero, Maneki</a>"
    " @ The Midway (San Francisco)<td>progressive house, tech house, EDM"
    "<td>$68-80 | 21+<td><td><div class='shrink'>2026/07/04</div></tr></table>"
)

RA_PAYLOAD = {
    "request_variables": {"filters": {"areas": {"eq": 218}}},
    "extract_ts_utc": "2026-07-08T07:52:50+00:00",
    "response": {"data": {"eventListings": {"totalResults": 250, "data": [
        {"event": {"id": "2480119", "title": "Trevor's Birthday Jam",
                   "date": "2026-07-08T00:00:00.000",
                   "startTime": "2026-07-08T21:00:00.000",
                   "endTime": "2026-07-09T02:00:00.000",
                   "attending": 12, "isTicketed": True, "cost": "0-10",
                   "contentUrl": "/events/2480119",
                   "venue": {"id": "91478", "name": "F8 1192 Folsom"},
                   "artists": [{"id": "1", "name": "starfari"}, {"id": "2", "name": "SNAQ"}],
                   "genres": [{"name": "Techno"}, {"name": "Tech House"}]}},
        {"event": {"id": None, "title": "id-less event dropped"}},
    ]}}},
}

TICKETPAGE_PAYLOAD = [
    {"ticket_url": "https://shotgun.live/en/events/ffdjuly26",
     "extract_ts_utc": "2026-07-08T07:53:06+00:00",
     "event_ld": [{"@type": "MusicEvent", "name": "Five Finger Disco",
                   "startDate": "2026-07-12T21:00",
                   "location": {"name": "White Horse Inn"},
                   "eventStatus": "https://schema.org/EventScheduled",
                   "offers": [{"@type": "Offer", "name": "GA", "price": "10",
                               "priceCurrency": "USD",
                               "availability": "https://schema.org/InStock"}]}]},
]


def test_nineteenhz_rows_snapshot_grain_and_types():
    rows = scene.nineteenhz_rows(HTML, "2026-07-08", "2026-07-08T00:00:00+00:00")
    assert len(rows) == 1
    r = rows[0]
    assert r["snapshot_date"] == "2026-07-08"       # observation day = dt partition
    assert r["event_date"] == "2026-07-04"
    assert r["venue"] == "The Midway"
    assert r["price_min"] == 68.0 and r["price_max"] == 80.0
    assert r["is_free"] is False
    assert r["n_artists"] == 3
    # same listing captured on two days -> two distinct keys (daily history)
    other = scene.nineteenhz_rows(HTML, "2026-07-09", "x")[0]
    assert other["nineteenhz_snapshot_id"] != r["nineteenhz_snapshot_id"]


def test_ra_rows_keep_attending_and_drop_idless():
    rows = scene.ra_rows(RA_PAYLOAD, "2026-07-08", "2026-07-08T00:00:00+00:00")
    assert len(rows) == 1                            # the id-less event is dropped
    r = rows[0]
    assert r["ra_event_id"] == "2480119"
    assert r["attending"] == 12
    assert r["event_date"] == "2026-07-08"
    assert r["artists"] == "starfari|SNAQ"
    assert r["genres"] == "Techno, Tech House"
    # daily re-observation -> new key per day (the attending time-series)
    assert (scene.ra_rows(RA_PAYLOAD, "2026-07-09", "x")[0]["ra_snapshot_id"]
            != r["ra_snapshot_id"])


def test_ticketpage_rows_flatten_offers():
    rows = scene.ticketpage_rows(TICKETPAGE_PAYLOAD, "2026-07-08", "x")
    assert len(rows) == 1
    r = rows[0]
    assert r["availability"] == "InStock"
    assert r["price_min"] == 10.0 and r["price_max"] == 10.0  # LD string -> float
    assert r["start_date"] == "2026-07-12"
    assert r["event_status"] == "EventScheduled"


def test_valid_date_rejects_garbage():
    assert scene._valid_date("2026-07-08T21:00") == "2026-07-08"
    assert scene._valid_date("Cascadia") is None
    assert scene._valid_date(None) is None


def test_dedupe_last_wins():
    rows = [{"k": "a", "v": 1}, {"k": "a", "v": 2}, {"k": "b", "v": 3}]
    out = {r["k"]: r["v"] for r in scene.dedupe_last(rows, "k")}
    assert out == {"a": 2, "b": 3}


def test_schemas_match_transform_outputs():
    for name, fixture in [("nineteenhz", scene.nineteenhz_rows(HTML, "2026-07-08", "x")),
                          ("ra", scene.ra_rows(RA_PAYLOAD, "2026-07-08", "x")),
                          ("ticketpages", scene.ticketpage_rows(TICKETPAGE_PAYLOAD, "2026-07-08", "x"))]:
        cols = [c for c, _ in scene.SOURCES[name]["schema"]]
        assert list(fixture[0].keys()) == cols
