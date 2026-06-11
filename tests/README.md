# Tests

Unit tests for the scheduled pipeline code. Nothing here touches the network
or GCP — Ticketmaster responses and GCS uploads are faked, so the suite runs
offline in any Python 3.11+ environment with pytest.

```bash
# From the repo root:
python3 -m pytest tests/ -v
```

## What is covered (and how to trace a failure)

`test_ticketmaster_daily.py` exercises `cloud_functions/ticketmaster_daily/main.py`
along the three surfaces where the daily job can break:

| Failing test group | Where to look |
|---|---|
| `test_state_codes_*`, `test_fetch_json_*`, `test_search_*filters*`, `test_search_slices_*` | Request building — API key, state list, env-var filters, or date-slice windows not reaching the URL |
| `test_search_paginates_*`, `*_max_pages_*`, `*_call_budget_*`, `test_run_enforces_max_calls_*` | Pagination + quota guard — wrong stop condition, or a run could blow the 5,000-call/day quota |
| `test_upload_raw_*`, `test_run_uploads_*`, `test_run_isolates_*`, `test_run_raises_when_all_*` | Bronze landing — per-state blob layout (`ticketmaster/dt=.../ticketmaster_<STATE>_...json`), failure isolation, all-states-failed retry signal |
| `test_flatten_*`, `test_dedupe_*`, `test_upsert_*`, `test_run_upserts_*`, `test_run_reports_silver_*` | Silver upsert — event_id-keyed MERGE into BigQuery `tm_events` (update if the id exists, insert if new, `first_seen_ts_utc` preserved) |
| `test_run_writes_summary_to_stderr_*`, `test_run_keeps_stderr_silent_*` | Alerting — failures must log at ERROR severity (that's what triggers the email alert), clean runs must not |

For a deployed run that fails (tests green but the cloud job errors), check:

```bash
gcloud functions logs read ticketmaster-daily-extract --region=us-west1 --limit=20
```

Each successful run logs a one-line JSON summary (`events_fetched`,
`api_calls`, `raw_uri`).
