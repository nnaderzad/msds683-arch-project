# Event Demand API

FastAPI skeleton for the live music event demand app.

This is the E1 version: endpoints return two stubbed records shaped like the future
gold-layer response. E2 will keep the endpoint contract and replace the stub data
with BigQuery queries over `fact_event_demand` and forecast tables.

## Run Locally

```bash
python -m pip install -r api/requirements.txt
uvicorn api.app:app --reload
```

Then open:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/shows
http://127.0.0.1:8000/show/demo_001
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Basic smoke check for local and deployed runs. |
| `GET` | `/shows` | List shows for the frontend dropdown. |
| `GET` | `/show/{event_id}` | Return one show by event id. |

## Stub Response Shape

The stub fields are chosen to match the planned gold/API contract:

- event, artist, venue, and show-date fields identify the show
- price and status fields come from Ticketmaster
- `local_interest` comes from Google Trends
- YouTube subscriber/view fields represent artist attention signals
- `forecast_price` is a placeholder for the later model output
