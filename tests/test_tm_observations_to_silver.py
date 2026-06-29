"""Offline tests for the bronze -> tm_observations transform (honest TM price history).

Pure-transform unit tests only — BigQuery/GCS I/O is isolated in iter_bronze /
upsert_to_silver and never touched here. Validates the locked within-day rule:
union presence, priced-if-any latest price, n_captures, price_disagreed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))                                   # common.keys
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "silver"))          # tm_observations_to_silver

import tm_observations_to_silver as t  # noqa: E402
from common import keys  # noqa: E402

LOAD_TS = "2026-06-28T00:00:00+00:00"


def _event(event_id="EV1", price_min=50.0, price_max=80.0, price_type="standard",
           local_date="2026-07-01", status="onsale", extra_ranges=None):
    """A raw Ticketmaster event as bronze lands it (array element). price_min=None -> unpriced."""
    ranges = []
    if extra_ranges:
        ranges.extend(extra_ranges)
    if price_min is not None:
        ranges.append({"type": price_type, "currency": "USD", "min": price_min, "max": price_max})
    return {
        "id": event_id,
        "dates": {"start": {"localDate": local_date}, "status": {"code": status}},
        "sales": {"public": {"startDateTime": "2026-05-01T17:00:00Z",
                             "endDateTime": "2026-07-01T23:00:00Z"}},
        "priceRanges": ranges,
    }


def _payload(dt, stamp, events):
    return (dt, stamp, events)


# --- single-event flatten ---------------------------------------------------

def test_event_to_obs_row_flattens_price_subset():
    row = t.event_to_obs_row(_event(), "2026-06-27", "20260627T030002Z")
    assert row["event_id"] == "EV1"
    assert row["snapshot_date"] == "2026-06-27"
    assert row["local_date"] == "2026-07-01"
    assert row["status_code"] == "onsale"
    assert row["price_type"] == "standard"
    assert row["price_currency"] == "USD"
    assert row["price_min"] == 50.0 and row["price_max"] == 80.0
    assert row["public_sale_start_utc"] == "2026-05-01T17:00:00Z"


def test_first_price_range_prefers_standard():
    ev = _event(price_min=None, extra_ranges=[
        {"type": "resale", "currency": "USD", "min": 200, "max": 400},
        {"type": "standard", "currency": "USD", "min": 60, "max": 90},
    ])
    row = t.event_to_obs_row(ev, "2026-06-27", "s")
    assert (row["price_type"], row["price_min"]) == ("standard", 60.0)


def test_event_without_price_is_null_not_dropped():
    row = t.event_to_obs_row(_event(price_min=None), "2026-06-27", "s")
    assert row is not None
    assert row["price_min"] is None and row["price_max"] is None
    assert row["price_type"] is None and row["price_currency"] is None


def test_event_without_id_skipped():
    ev = _event()
    del ev["id"]
    assert t.event_to_obs_row(ev, "2026-06-27", "s") is None


# --- within-day aggregation (the locked rule) -------------------------------

def test_union_presence_and_n_captures():
    # Same event observed by 3 of the day's runs -> one row, n_captures = 3.
    payloads = [
        _payload("2026-06-27", "20260627T030002Z", [_event(price_min=50.0)]),
        _payload("2026-06-27", "20260627T070002Z", [_event(price_min=50.0)]),
        _payload("2026-06-27", "20260627T110002Z", [_event(price_min=50.0)]),
    ]
    rows = t.rows_from_payloads(payloads, LOAD_TS)
    assert len(rows) == 1
    assert rows[0]["n_captures"] == 3
    assert rows[0]["price_disagreed"] is False
    assert rows[0]["tm_obs_id"] == keys.snapshot_id("tm", "EV1", "2026-06-27")


def test_priced_if_any_latest_priced_wins():
    # 03:00 priced $50, 07:00 partial run dropped the price (None), 11:00 priced $55.
    # latest *priced* capture wins -> $55; a later unpriced run never nulls a known price.
    payloads = [
        _payload("2026-06-27", "20260627T030002Z", [_event(price_min=50.0, price_max=70.0)]),
        _payload("2026-06-27", "20260627T070002Z", [_event(price_min=None)]),
        _payload("2026-06-27", "20260627T110002Z", [_event(price_min=55.0, price_max=75.0)]),
    ]
    row = t.rows_from_payloads(payloads, LOAD_TS)[0]
    assert row["price_min"] == 55.0 and row["price_max"] == 75.0
    assert row["n_captures"] == 3
    assert row["price_disagreed"] is True            # 50 vs 55, and one unpriced


def test_within_file_pagination_dups_count_as_one_capture():
    # Bronze raw files repeat an event across pages -> SAME capture stamp twice. That is one
    # run, so n_captures must be 1 (not 2); within-file dups collapse, priced occurrence wins.
    payloads = [
        _payload("2026-06-27", "20260627T030002Z",
                 [_event(price_min=None), _event(price_min=50.0)]),  # same event, same file
    ]
    rows = t.rows_from_payloads(payloads, LOAD_TS)
    assert len(rows) == 1
    assert rows[0]["n_captures"] == 1
    assert rows[0]["price_min"] == 50.0           # priced occurrence preferred within the file
    assert rows[0]["price_disagreed"] is False    # one capture cannot disagree with itself


def test_all_unpriced_day_stays_null():
    payloads = [
        _payload("2026-06-27", "20260627T030002Z", [_event(price_min=None)]),
        _payload("2026-06-27", "20260627T070002Z", [_event(price_min=None)]),
    ]
    row = t.rows_from_payloads(payloads, LOAD_TS)[0]
    assert row["price_min"] is None
    assert row["price_disagreed"] is False           # consistently unpriced, not a conflict
    assert row["n_captures"] == 2


def test_disagreement_flag_on_some_priced_some_not():
    payloads = [
        _payload("2026-06-27", "20260627T030002Z", [_event(price_min=50.0)]),
        _payload("2026-06-27", "20260627T070002Z", [_event(price_min=None)]),
    ]
    row = t.rows_from_payloads(payloads, LOAD_TS)[0]
    assert row["price_min"] == 50.0
    assert row["price_disagreed"] is True


def test_distinct_days_and_events_kept_separate():
    payloads = [
        _payload("2026-06-26", "20260626T030002Z", [_event(event_id="EV1", price_min=50.0)]),
        _payload("2026-06-27", "20260627T030002Z", [_event(event_id="EV1", price_min=50.0),
                                                     _event(event_id="EV2", price_min=20.0)]),
    ]
    rows = t.rows_from_payloads(payloads, LOAD_TS)
    keysset = {(r["event_id"], r["snapshot_date"]) for r in rows}
    assert keysset == {("EV1", "2026-06-26"), ("EV1", "2026-06-27"), ("EV2", "2026-06-27")}


def test_non_list_payload_ignored():
    payloads = [_payload("2026-06-27", "s", {"error": "not a list"})]
    assert t.rows_from_payloads(payloads, LOAD_TS) == []


def test_stamp_and_dt_extraction():
    name = "ticketmaster/dt=2026-06-27/ticketmaster_CA_20260627T030002Z.json"
    assert t.dt_from_path(name) == "2026-06-27"
    assert t.stamp_from_path(name) == "20260627T030002Z"
    # early nationwide single-file form (no state code in the name)
    assert t.stamp_from_path("ticketmaster/dt=2026-06-08/ticketmaster_20260608T182948Z.json") \
        == "20260608T182948Z"
