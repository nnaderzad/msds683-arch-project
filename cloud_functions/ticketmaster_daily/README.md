# Ticketmaster nationwide extract (Cloud Run function)

Scheduled pipeline that snapshots upcoming Ticketmaster events for **all 50
states + DC** into the bronze (raw) bucket, one file per state. Everything is
deployed by Terraform — see `terraform/ticketmaster_scheduler.tf`.

```
Cloud Scheduler (06:00 + 18:00 PT)
  --OIDC HTTP POST-->  Cloud Run function gen2 "ticketmaster-daily-extract"
  --Discovery API-->   per state: 180-day window in 15-day slices, paged
  --writes-->          gs://<project>-raw/ticketmaster/dt=YYYY-MM-DD/ticketmaster_<STATE>_<stamp>.json
```

## API quota math (5,000 calls/day)

- Ticketmaster caps deep paging at `size * page < 1000`, so one filter set can
  never return more than 1,000 events. Slicing each state's 180-day window
  into 15-day chunks gives **every slice its own 1,000-event budget** —
  that's what makes coverage complete, not extra calls on the same filters.
- A typical nationwide run uses **~700–900 calls** (most states need 1 page
  per slice; CA/NY/TX/FL need more).
- `MAX_CALLS_PER_RUN=1200` is a hard stop. Worst case:
  2 runs/day × (1 attempt + 1 Scheduler retry) × 1,200 = **4,800 < 5,000**.
  This is why `retry_count` is 1 and the schedule is 2×/day — raising either
  requires redoing this math.
- The 0.25 s pause between requests respects the 5 calls/second rate limit.

## Failure isolation (traceability)

Each state is fetched and uploaded independently:

- One state failing → recorded in the run summary (`failed_states`), sweep
  continues. No retry, because a full retry re-spends the whole call budget.
- All states failing → the function raises (5xx), which is what triggers
  Cloud Scheduler's retry — that pattern means an outage, not a blip.
- Budget exhausted mid-sweep → remaining states land in `skipped_states`.

Every run logs a one-line JSON summary: `states_swept`, `states_uploaded`,
`events_fetched`, `api_calls`, `failed_states`, `skipped_states`.

## Configuration

All knobs are env vars set in Terraform (`service_config.environment_variables`):

| Var | Default | Meaning |
|---|---|---|
| `STATE_CODES` | `ALL` | `ALL` = 50 states + DC, or a list like `CA,NY,TX` |
| `CLASSIFICATION_NAME` | `music` | Event classification |
| `DAYS_AHEAD` | `180` | Upcoming-event window |
| `SLICE_DAYS` | `15` | Date-slice width (smaller = more coverage headroom, more calls) |
| `PAGE_SIZE` | `200` | Events per API page (Ticketmaster max) |
| `MAX_PAGES` | `5` | Page cap per slice (deep-paging limit) |
| `MAX_CALLS_PER_RUN` | `1200` | Hard API-call stop per run (quota guard) |

`TICKETMASTER_API_KEY` is injected from Secret Manager (`ticketmaster-api-key`),
sourced from `ticketmaster_api_key` in the git-ignored `terraform.tfvars`.

## Deploy / operate

```bash
cd terraform
terraform apply     # zips this directory and (re)deploys on any code change

# Trigger a run right now instead of waiting for the schedule:
gcloud scheduler jobs run ticketmaster-daily-extract --location=us-west1

# Check run history / summaries:
gcloud functions logs read ticketmaster-daily-extract --region=us-west1 --limit=30
```

Unit tests live in `tests/` (offline, no GCP/network needed):
`python3 -m pytest tests/ -v`

## Relationship to the POC

`ticketmaster_api/ticketmaster_poc.py` remains the local/manual exploration
tool (CSV preview, richness summary). This function inlines the same fetch +
raw-landing logic (Cloud Functions uploads only this directory, so it can't
import `common/gcs_io.py`), extended with state sweeping, date slicing, and
the quota guard.
