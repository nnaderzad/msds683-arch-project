# Dashboard Chart Assumptions

The F3 frontend reads live show data from the FastAPI service. The local mock show file is kept only as a fixture/reference shape for tests and development.

## Combined Signal Chart

- The x-axis uses calendar dates.
- Observed Ticketmaster price is plotted as `price_min`, the lowest available price.
- Forecast price is the model's predicted `price_min`, so it is plotted on the same right-side dollar axis as observed lowest price.
- The forecast line is dashed because it is projected data, not observed API data.
- Forecast dates are derived from `show_date - days_to_show`.
- Google Trends is already a 0-100 local interest index, so it stays on the left 0-100 axis.
- YouTube views can be much larger than Trends, so the UI indexes YouTube views to the same 0-100 visual scale for comparison.
- Missing source signals do not remove the show. The chart disables unavailable signal toggles and keeps null values visible as a data-quality issue.

## API Wiring

The chart expects the API shape from `GET /show/{event_id}`:

- one selected event/show summary
- `history[]` with dated observed price, Google Trends, and YouTube values
- `forecast[]` with `days_to_show` and `predicted_price`

The frontend uses `GET /shows` to populate the dropdown. It ranks the known hero/demo IDs first, then prefers shows with forecast, price, Trends, and YouTube coverage, and caps the dropdown to keep the UI usable. Configure the API host with `VITE_API_BASE_URL`; if unset, the app defaults to `http://127.0.0.1:8000`.
