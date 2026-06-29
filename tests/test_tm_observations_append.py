"""Offline tests for the cloud function's honest-observation append (tm_observations).

Validates the pure row-shaping and that append_observations issues the expected
CREATE / staging-load / MERGE, with the locked incremental rule in the MERGE. The
within-day aggregation *math* is covered by tests/test_tm_observations_to_silver.py
(the batch backfill shares the canonical rule); here we check the CF wiring.

google.cloud / functions_framework are stubbed by tests/conftest.py when absent.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))  # common.keys cross-consistency check

from common import keys  # noqa: E402

RUN_TS = datetime(2026, 6, 27, 3, 0, 2, tzinfo=timezone.utc)


def _flat(event_id="EV1", price_min=50.0, price_max=80.0, local_date="2026-07-01"):
    """A flattened tm_events-style row as run() builds via flatten_event()."""
    return {
        "event_id": event_id,
        "local_date": local_date,
        "status_code": "onsale",
        "price_type": "standard" if price_min is not None else None,
        "price_currency": "USD" if price_min is not None else None,
        "price_min": price_min,
        "price_max": price_max,
        "public_sale_start_utc": "2026-05-01T17:00:00Z",
        "public_sale_end_utc": "2026-07-01T23:00:00Z",
    }


# --- pure row shaping -------------------------------------------------------

def test_observation_rows_shape_and_key(tm_main):
    rows = tm_main.observation_rows([_flat()], RUN_TS)
    assert len(rows) == 1
    r = rows[0]
    assert r["snapshot_date"] == "2026-06-27"          # the run's UTC date = observation day
    assert r["event_id"] == "EV1"
    assert r["price_min"] == 50.0 and r["price_max"] == 80.0
    assert r["price_currency"] == "USD"
    # deterministic id matches common/keys.py exactly (CF replicates the hash)
    assert r["tm_obs_id"] == keys.snapshot_id("tm", "EV1", "2026-06-27")


def test_observation_rows_skip_missing_id_and_keep_unpriced(tm_main):
    rows = tm_main.observation_rows([_flat(event_id=None), _flat(price_min=None)], RUN_TS)
    assert len(rows) == 1                               # the id-less row dropped
    assert rows[0]["price_min"] is None                # observed unpriced -> NULL, not dropped


# --- MERGE wiring -----------------------------------------------------------

class _FakeJob:
    num_dml_affected_rows = 7

    def result(self):
        return None


class _FakeBQ:
    def __init__(self, project=None):
        self.queries = []
        self.loaded = []

    def query(self, sql):
        self.queries.append(sql)
        return _FakeJob()

    def load_table_from_json(self, rows, table, job_config=None):
        self.loaded.append((table, list(rows)))
        return _FakeJob()


def test_append_observations_issues_create_load_merge(tm_main, base_env, monkeypatch):
    fake = _FakeBQ()
    monkeypatch.setattr(tm_main.bigquery, "Client", lambda project=None: fake)

    affected = tm_main.append_observations([_flat(), _flat(event_id="EV2", price_min=None)], RUN_TS)
    assert affected == 7

    create = next(q for q in fake.queries if q.startswith("CREATE TABLE IF NOT EXISTS"))
    assert "tm_observations" in create and "PARTITION BY snapshot_date" in create

    assert fake.loaded, "should load a staging table"
    staging_table, staging_rows = fake.loaded[0]
    assert staging_table.endswith("tm_observations_cf_staging")
    assert len(staging_rows) == 2

    merge = next(q for q in fake.queries if q.startswith("MERGE"))
    assert "ON T.event_id = S.event_id AND T.snapshot_date = S.snapshot_date" in merge
    assert "COALESCE(S.price_min, T.price_min)" in merge      # priced-if-any, latest wins
    assert "T.n_captures = T.n_captures + 1" in merge
    assert "1, FALSE, S.load_ts_utc" in merge                # new (event, day) row inserts


def test_append_observations_empty_is_noop(tm_main, base_env, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(tm_main.bigquery, "Client",
                        lambda project=None: called.__setitem__("n", called["n"] + 1))
    assert tm_main.append_observations([{"event_id": None}], RUN_TS) == 0
    assert called["n"] == 0                                  # no BigQuery client built
