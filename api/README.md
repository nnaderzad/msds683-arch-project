# Event Demand API

FastAPI service for the live music event demand app.

The E2 version reads the gold BigQuery layer and serves show summaries, per-show
history, and precomputed price forecasts. Forecasts are generated upstream into
`forecast_event_price`; the API does not run the model at request time.

## Run Locally

```bash
python -m pip install -r api/requirements.txt
uvicorn api.app:app --reload
```

Then open:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/shows
http://127.0.0.1:8000/show/<event_id>
```

By default the API reads:

```text
project: data-architecture-498123
dataset: event_demand_analytics
```

Override with:

```bash
export DBT_GCP_PROJECT=<project>
export DBT_BQ_DATASET=<dataset>
```

Live BigQuery reads use Application Default Credentials:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project data-architecture-498123
gcloud config set project data-architecture-498123
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Basic smoke check for local and deployed runs. |
| `GET` | `/shows` | List shows for the frontend dropdown. |
| `GET` | `/show/{event_id}` | Return one show summary plus history and forecast series. |

## Response Shape

`/shows` returns one latest-snapshot summary per event:

- event, artist, venue, and show-date fields identify the show
- price and status fields come from Ticketmaster
- `local_interest` comes from Google Trends
- `yt_subscribers` and `yt_views` represent YouTube artist attention signals
- `forecast_price` comes from the nearest-to-show forecast row

`/show/{event_id}` returns the same summary plus:

- `history`: daily event snapshots from `fact_event_demand`
- `forecast`: precomputed forecast rows from `forecast_event_price`

Nullable gold signals serialize as JSON `null`.

## Container

```bash
docker build -f api/Dockerfile -t event-demand-api .
docker run -p 8080:8080 event-demand-api
```
