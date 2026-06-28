"""D2 / C4-deferred — GX forecast sanity suite for `forecast_event_price`.

Validates the precomputed price forecast: non-negative, finite predictions, valid
horizon, and a unique (event_id, days_to_show) grain. Decoupled — takes a forecast
dataframe, so it works on the offline seed-generated forecast (CI) and the live gold
table alike.
"""

from __future__ import annotations

import great_expectations as gx


def build_forecast_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="gold_forecast_event_price")
    suite.add_expectation(gx.expectations.ExpectTableRowCountToBeBetween(min_value=1))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="event_id"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="predicted_price"))
    # Price floor: a forecast is never negative (and never absurd).
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(
        column="predicted_price", min_value=0, max_value=100_000))
    # Forecast horizon is toward the show date (>= 0 days out).
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="days_to_show", min_value=0))
    # One forecast per event per day-out.
    suite.add_expectation(gx.expectations.ExpectCompoundColumnsToBeUnique(
        column_list=["event_id", "days_to_show"]))
    return suite


def run_forecast_suite(forecast_df, project_root=None):
    """Validate a forecast dataframe against the sanity suite. Returns the GX result."""
    import gx_project

    context = gx_project.get_context(project_root)
    return gx_project.run_suite_on_dataframe(
        context, forecast_df, build_forecast_suite(), key="gold_forecast_event_price"
    )
