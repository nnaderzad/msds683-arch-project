"""Unit tests for cloud_functions/ticketmaster_daily/main.py.

Covers the failure surfaces of the nationwide sweep so a bad run can be
traced to one layer quickly:
  1. request building   — API key, state filters, and date slices in the URL
  2. pagination + quota — stops at the last page, honors per-run call budget
  3. raw landing        — per-state bronze blobs, state-failure isolation,
                          and the all-states-failed retry signal
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

RUN_TS = datetime(2026, 6, 11, 14, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    """Stands in for the urlopen context manager."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeBlob:
    def __init__(self, name: str, store: dict):
        self.name = name
        self._store = store

    def upload_from_string(self, data: str, content_type: str):
        self._store[self.name] = {"data": data, "content_type": content_type}


class FakeStorageClient:
    """Records uploads in .uploads instead of touching GCS."""

    def __init__(self, project: str | None = None):
        self.project = project
        self.uploads: dict[str, dict] = {}

    def bucket(self, name: str):
        client = self

        class _Bucket:
            def blob(self, blob_name: str):
                return FakeBlob(blob_name, client.uploads)

        return _Bucket()


def page_payload(events: list[dict], page_number: int, total_pages: int) -> dict:
    """Shape one /events.json response the way Ticketmaster does."""
    return {
        "_embedded": {"events": events},
        "page": {"number": page_number, "totalPages": total_pages},
    }


@pytest.fixture
def no_sleep(tm_main, monkeypatch):
    """Skip the rate-limit pause so tests run instantly."""
    monkeypatch.setattr(tm_main.time, "sleep", lambda _s: None)


@pytest.fixture
def fake_gcs(tm_main, monkeypatch):
    """Swap the GCS client for an in-memory fake; returns it for assertions."""
    client = FakeStorageClient()
    monkeypatch.setattr(tm_main.storage, "Client", lambda project: client)
    return client


class FakeBQJob:
    def __init__(self, affected: int = 0):
        self.num_dml_affected_rows = affected

    def result(self):
        return self


class FakeBQClient:
    """Records queries and staged rows instead of touching BigQuery."""

    def __init__(self, project: str | None = None):
        self.project = project
        self.queries: list[str] = []
        self.loaded_rows: list[dict] | None = None
        self.load_target: str | None = None
        self.merge_affected = 0

    def query(self, sql: str):
        self.queries.append(sql)
        return FakeBQJob(self.merge_affected)

    def load_table_from_json(self, rows, table, job_config=None):
        self.loaded_rows = list(rows)
        self.load_target = table
        return FakeBQJob()


@pytest.fixture
def fake_bq(tm_main, monkeypatch):
    """Swap the BigQuery client for an in-memory fake; returns it."""
    client = FakeBQClient()
    monkeypatch.setattr(tm_main.bigquery, "Client", lambda project: client)
    return client


# ---------------------------------------------------------------------------
# 1. Request building (state list, filters, date slicing)
# ---------------------------------------------------------------------------


def test_state_codes_default_is_all_50_plus_dc(tm_main, monkeypatch):
    monkeypatch.delenv("STATE_CODES", raising=False)
    codes = tm_main.state_codes()
    assert len(codes) == 51
    assert "CA" in codes and "NY" in codes and "DC" in codes
    assert len(set(codes)) == 51  # no duplicates


def test_state_codes_env_override(tm_main, monkeypatch):
    monkeypatch.setenv("STATE_CODES", "ca, ny")
    assert tm_main.state_codes() == ["CA", "NY"]


def test_fetch_json_sends_api_key_and_params(tm_main, base_env, monkeypatch):
    seen_urls = []

    def fake_urlopen(request, timeout, **kwargs):
        seen_urls.append(request.full_url)
        return FakeResponse({"ok": True})

    monkeypatch.setattr(tm_main, "urlopen", fake_urlopen)

    result = tm_main.fetch_json("events.json", {"stateCode": "CA", "page": 0})

    assert result == {"ok": True}
    parsed = urlparse(seen_urls[0])
    query = parse_qs(parsed.query)
    assert parsed.path.endswith("/discovery/v2/events.json")
    assert query["apikey"] == ["test-key"]
    assert query["stateCode"] == ["CA"]


def test_search_slices_window_into_disjoint_ranges(
    tm_main, base_env, monkeypatch, no_sleep
):
    monkeypatch.setenv("DAYS_AHEAD", "30")
    monkeypatch.setenv("SLICE_DAYS", "15")
    seen_params = []

    def fake_fetch(endpoint, params):
        seen_params.append(params)
        return page_payload([], 0, 1)

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    tm_main.search_state_events("CA", RUN_TS, calls_remaining=100)

    # 30 days / 15-day slices = 2 slices, each queried once (empty results).
    assert len(seen_params) == 2
    assert seen_params[0]["startDateTime"] == "2026-06-11T14:30:00Z"
    assert seen_params[0]["endDateTime"] == "2026-06-26T14:29:59Z"  # 1s gap
    assert seen_params[1]["startDateTime"] == "2026-06-26T14:30:00Z"
    assert seen_params[1]["endDateTime"] == "2026-07-11T14:29:59Z"
    assert all(p["stateCode"] == "CA" for p in seen_params)


def test_search_passes_classification_from_env(
    tm_main, base_env, monkeypatch, no_sleep
):
    monkeypatch.setenv("CLASSIFICATION_NAME", "comedy")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")
    seen_params = []

    def fake_fetch(endpoint, params):
        seen_params.append(params)
        return page_payload([], 0, 1)

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    tm_main.search_state_events("NY", RUN_TS, calls_remaining=100)

    assert seen_params[0]["classificationName"] == "comedy"


# ---------------------------------------------------------------------------
# 2. Pagination + per-run call budget
# ---------------------------------------------------------------------------


def test_search_paginates_within_slice_until_last_page(
    tm_main, base_env, monkeypatch, no_sleep
):
    monkeypatch.setenv("DAYS_AHEAD", "10")  # single slice
    monkeypatch.setenv("SLICE_DAYS", "15")
    pages = [
        page_payload([{"id": "e1"}, {"id": "e2"}], 0, 2),
        page_payload([{"id": "e3"}], 1, 2),
    ]

    def fake_fetch(endpoint, params):
        return pages[params["page"]]

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    events, calls = tm_main.search_state_events("CA", RUN_TS, calls_remaining=100)

    assert [e["id"] for e in events] == ["e1", "e2", "e3"]
    assert calls == 2  # did not request a third page


def test_search_honors_max_pages_per_slice(tm_main, base_env, monkeypatch, no_sleep):
    monkeypatch.setenv("DAYS_AHEAD", "10")  # single slice
    monkeypatch.setenv("SLICE_DAYS", "15")
    monkeypatch.setenv("MAX_PAGES", "2")

    def fake_fetch(endpoint, params):
        # Pretend there are always more pages; only the cap should stop us.
        return page_payload([{"id": f"e{params['page']}"}], params["page"], 99)

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    events, calls = tm_main.search_state_events("CA", RUN_TS, calls_remaining=100)

    assert calls == 2
    assert len(events) == 2


def test_search_stops_when_call_budget_exhausted(
    tm_main, base_env, monkeypatch, no_sleep
):
    monkeypatch.setenv("DAYS_AHEAD", "180")  # 12 slices available

    def fake_fetch(endpoint, params):
        return page_payload([{"id": "e"}], 0, 1)

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    events, calls = tm_main.search_state_events("CA", RUN_TS, calls_remaining=3)

    assert calls == 3  # quota guard: never exceeds the remaining budget


def test_run_enforces_max_calls_across_states(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs
):
    monkeypatch.setenv("STATE_CODES", "CA,NY,TX")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")
    monkeypatch.setenv("MAX_CALLS_PER_RUN", "2")  # only 2 calls for 3 states

    def fake_fetch(endpoint, params):
        return page_payload([{"id": f"{params['stateCode']}-e"}], 0, 1)

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    summary = tm_main.run(request=None)

    assert summary["api_calls"] == 2
    assert summary["skipped_states"] == ["TX"]  # budget ran out before TX


# ---------------------------------------------------------------------------
# 3. Raw landing + failure isolation
# ---------------------------------------------------------------------------


def test_upload_raw_uses_per_state_partitioned_layout(
    tm_main, base_env, fake_gcs
):
    uri = tm_main.upload_raw("CA", [{"id": "e1"}], RUN_TS)

    expected_blob = "ticketmaster/dt=2026-06-11/ticketmaster_CA_20260611T143000Z.json"
    assert uri == f"gs://test-project-raw/{expected_blob}"
    upload = fake_gcs.uploads[expected_blob]
    assert upload["content_type"] == "application/json"
    assert json.loads(upload["data"]) == [{"id": "e1"}]


def test_run_uploads_one_blob_per_state_with_events(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs
):
    monkeypatch.setenv("STATE_CODES", "CA,WY")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")

    def fake_fetch(endpoint, params):
        if params["stateCode"] == "CA":
            return page_payload([{"id": "ca-1"}, {"id": "ca-2"}], 0, 1)
        return page_payload([], 0, 0)  # WY: nothing on sale

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    summary = tm_main.run(request=None)

    assert summary["states_swept"] == 2
    assert summary["states_uploaded"] == 1  # empty WY produces no blob
    assert summary["events_fetched"] == 2
    assert summary["failed_states"] == {}
    # run() stamps with the real clock, so assert the layout rather than an
    # exact timestamp: one CA blob, in the dt= partition, nothing for WY.
    blob_names = list(fake_gcs.uploads)
    assert len(blob_names) == 1
    assert blob_names[0].startswith("ticketmaster/dt=")
    assert "_CA_" in blob_names[0] and blob_names[0].endswith(".json")


def test_run_isolates_single_state_failure(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs
):
    monkeypatch.setenv("STATE_CODES", "CA,NY")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")

    def fake_fetch(endpoint, params):
        if params["stateCode"] == "CA":
            raise OSError("Ticketmaster 503")
        return page_payload([{"id": "ny-1"}], 0, 1)

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    # One bad state must NOT abort the sweep (a full retry would re-spend the
    # whole call budget) — it is reported in the summary instead.
    summary = tm_main.run(request=None)

    assert "CA" in summary["failed_states"]
    assert "503" in summary["failed_states"]["CA"]
    assert summary["states_uploaded"] == 1  # NY still landed


# ---------------------------------------------------------------------------
# 4. Silver layer: flatten + upsert keyed on event_id
# ---------------------------------------------------------------------------


SAMPLE_EVENT = {
    "id": "evt-1",
    "name": "Sample Show",
    "type": "event",
    "url": "https://tm.example/evt-1",
    "dates": {
        "start": {"localDate": "2026-07-04", "localTime": "20:00:00",
                  "dateTime": "2026-07-05T03:00:00Z"},
        "timezone": "America/Los_Angeles",
        "status": {"code": "onsale"},
    },
    "priceRanges": [
        {"type": "vip", "currency": "USD", "min": 200.0, "max": 400.0},
        {"type": "standard", "currency": "USD", "min": 45.0, "max": 95.0},
    ],
    "classifications": [
        {"primary": True,
         "segment": {"name": "Music"},
         "genre": {"name": "Rock"},
         "subGenre": {"name": "Alternative Rock"}},
    ],
    "_embedded": {
        "venues": [{"id": "v-1", "name": "The Fillmore",
                    "city": {"name": "San Francisco"},
                    "state": {"stateCode": "CA"},
                    "country": {"countryCode": "US"},
                    "location": {"latitude": "37.78", "longitude": "-122.43"}}],
        "attractions": [{"id": "a-1", "name": "Band One"},
                        {"id": "a-2", "name": "Band Two"}],
    },
}


def test_flatten_event_produces_event_id_keyed_row(tm_main):
    row = tm_main.flatten_event(SAMPLE_EVENT, RUN_TS)

    assert row["event_id"] == "evt-1"
    assert row["price_type"] == "standard"  # standard preferred over vip
    assert row["price_min"] == 45.0
    assert row["genre"] == "Rock"
    assert row["venue_state_code"] == "CA"
    assert row["attraction_names"] == "Band One|Band Two"
    assert row["last_seen_ts_utc"] == "2026-06-11T14:30:00+00:00"
    # Every staging column must be present, or the BQ load would fail.
    assert set(row) == {name for name, _ in tm_main.STAGING_SCHEMA}


def test_dedupe_rows_keeps_one_row_per_event_id(tm_main):
    rows = [
        {"event_id": "evt-1", "status_code": "onsale"},
        {"event_id": "evt-2", "status_code": "onsale"},
        {"event_id": "evt-1", "status_code": "cancelled"},  # later snapshot
        {"event_id": None, "status_code": "onsale"},  # unkeyed -> dropped
    ]

    deduped = tm_main.dedupe_rows(rows)

    assert len(deduped) == 2
    evt1 = next(r for r in deduped if r["event_id"] == "evt-1")
    assert evt1["status_code"] == "cancelled"  # last occurrence wins


def test_upsert_merges_on_event_id_preserving_first_seen(
    tm_main, base_env, fake_bq
):
    fake_bq.merge_affected = 7
    rows = [tm_main.flatten_event(SAMPLE_EVENT, RUN_TS)]

    affected = tm_main.upsert_to_silver(rows)

    assert affected == 7
    assert fake_bq.loaded_rows == rows
    assert fake_bq.load_target == "test-project.test_dataset.tm_events_staging"

    create_sql, merge_sql = fake_bq.queries
    assert "CREATE TABLE IF NOT EXISTS" in create_sql
    assert "first_seen_ts_utc TIMESTAMP" in create_sql

    # The user-facing contract: upsert keyed on Ticketmaster's event id.
    assert "MERGE" in merge_sql
    assert "ON T.event_id = S.event_id" in merge_sql
    assert "WHEN MATCHED THEN UPDATE" in merge_sql
    assert "WHEN NOT MATCHED THEN INSERT" in merge_sql
    # first_seen is stamped on insert but never overwritten on update.
    update_clause = merge_sql.split("WHEN NOT MATCHED")[0]
    assert "T.first_seen_ts_utc" not in update_clause
    assert "T.last_seen_ts_utc = S.last_seen_ts_utc" in update_clause


def test_run_upserts_deduped_rows_into_silver(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs, fake_bq
):
    monkeypatch.setenv("STATE_CODES", "CA")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")
    fake_bq.merge_affected = 1
    monkeypatch.setattr(
        tm_main, "fetch_json", lambda e, p: page_payload([SAMPLE_EVENT], 0, 1)
    )

    summary = tm_main.run(request=None)

    assert summary["silver"]["rows_in"] == 1
    assert summary["silver"]["rows_upserted"] == 1
    assert summary["silver"]["error"] is None
    # After the merge, the deduped table is exported to the processed bucket.
    assert summary["silver"]["processed_export_uri"].startswith(
        "gs://test-project-processed/ticketmaster/dt="
    )
    assert fake_bq.loaded_rows[0]["event_id"] == "evt-1"


def test_export_to_processed_writes_parquet_snapshot(tm_main, base_env, fake_bq):
    uri = tm_main.export_to_processed(RUN_TS)

    assert uri == "gs://test-project-processed/ticketmaster/dt=2026-06-11/tm_events_*.parquet"
    export_sql = fake_bq.queries[-1]
    assert "EXPORT DATA" in export_sql
    assert "format='PARQUET'" in export_sql
    assert "overwrite=true" in export_sql  # same-day re-runs replace the export
    assert "test-project.test_dataset.tm_events" in export_sql


def test_run_reports_silver_failure_without_raising(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs
):
    monkeypatch.setenv("STATE_CODES", "CA")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")
    monkeypatch.setattr(
        tm_main, "fetch_json", lambda e, p: page_payload([SAMPLE_EVENT], 0, 1)
    )

    def exploding_upsert(rows):
        raise RuntimeError("BigQuery quota")

    monkeypatch.setattr(tm_main, "upsert_to_silver", exploding_upsert)

    # Raw snapshots already landed and the MERGE is idempotent on the next
    # run — a silver failure must not 5xx (that would re-spend API quota).
    summary = tm_main.run(request=None)

    assert summary["silver"]["error"] == "RuntimeError: BigQuery quota"
    assert summary["states_uploaded"] == 1


def test_run_raises_when_all_states_fail(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs
):
    monkeypatch.setenv("STATE_CODES", "CA,NY")

    def exploding_fetch(endpoint, params):
        raise OSError("Ticketmaster unreachable")

    monkeypatch.setattr(tm_main, "fetch_json", exploding_fetch)

    # Every state failing means an outage, not a blip: the handler must raise
    # so the 5xx makes Cloud Scheduler's retry_config kick in.
    with pytest.raises(RuntimeError, match="All 2 states failed"):
        tm_main.run(request=None)


# ---------------------------------------------------------------------------
# 5. Alert signal: failures must surface where monitoring can see them
# ---------------------------------------------------------------------------
# Cloud Logging marks stderr output as severity ERROR, and the alert policy in
# terraform/monitoring.tf emails on ERROR logs from this function. These tests
# pin that contract: any failed/skipped state or silver error -> summary on
# stderr; a fully clean run -> nothing on stderr (no false-alarm emails).


def test_run_writes_summary_to_stderr_when_a_state_fails(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs, fake_bq, capsys
):
    monkeypatch.setenv("STATE_CODES", "CA,NY")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")

    def fake_fetch(endpoint, params):
        if params["stateCode"] == "CA":
            raise OSError("Ticketmaster 503")
        return page_payload([SAMPLE_EVENT], 0, 1)

    monkeypatch.setattr(tm_main, "fetch_json", fake_fetch)

    tm_main.run(request=None)

    err = capsys.readouterr().err
    assert "failed_states" in err and "CA" in err


def test_run_writes_summary_to_stderr_when_silver_merge_fails(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs, capsys
):
    monkeypatch.setenv("STATE_CODES", "CA")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")
    monkeypatch.setattr(
        tm_main, "fetch_json", lambda e, p: page_payload([SAMPLE_EVENT], 0, 1)
    )

    def exploding_upsert(rows):
        raise RuntimeError("BigQuery quota")

    monkeypatch.setattr(tm_main, "upsert_to_silver", exploding_upsert)

    tm_main.run(request=None)

    assert "BigQuery quota" in capsys.readouterr().err


def test_run_keeps_stderr_silent_on_clean_run(
    tm_main, base_env, monkeypatch, no_sleep, fake_gcs, fake_bq, capsys
):
    monkeypatch.setenv("STATE_CODES", "CA")
    monkeypatch.setenv("DAYS_AHEAD", "10")
    monkeypatch.setenv("SLICE_DAYS", "15")
    monkeypatch.setattr(
        tm_main, "fetch_json", lambda e, p: page_payload([SAMPLE_EVENT], 0, 1)
    )

    summary = tm_main.run(request=None)

    assert summary["failed_states"] == {}
    assert capsys.readouterr().err == ""  # clean run -> no alert email
