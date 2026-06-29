# Dashboard Chart Assumptions

This frontend is still a scaffold for F1/F2, so it uses local mock show data shaped like the API/gold-table response we expect later.

## Combined Signal Chart

- The x-axis uses calendar dates.
- Observed Ticketmaster price is plotted as a midpoint: `(price_min + price_max) / 2`.
- Forecast price is plotted on the same right-side dollar axis as observed price.
- The forecast line is dashed because it is projected data, not observed API data.
- Forecast dates are derived from `show_date - days_to_show`.
- Google Trends is already a 0-100 local interest index, so it stays on the left 0-100 axis.
- YouTube views can be much larger than Trends, so the UI indexes YouTube views to the same 0-100 visual scale for comparison.
- Missing source signals do not remove the show. The chart disables unavailable signal toggles and keeps null values visible as a data-quality issue.

## Future F3/API Wiring

When the FastAPI endpoint serves live or BigQuery-backed data, the chart expects the same shape:

- one selected event/show summary
- `history[]` with dated observed price, Google Trends, and YouTube values
- `forecast[]` with `days_to_show` and `predicted_price`

The current mock forecast is demo data. The real forecast should come from the backend/model layer once that task is ready.
