# Text-to-SQL agent — evaluation report

Generated 2026-08-09T03:59:38+00:00 by `eda/eval_text_to_sql.py --runs 3` (model: gemini-2.5-flash).
Scoring: execution-result match against committed gold SQL (values-only multiset;
refusal questions pass on refused/blocked). Re-run the same command to refresh.

## Accuracy

| Tier | Questions | Runs | Accuracy |
|---|---|---|---|
| easy | 8 | 24 | 100% |
| join | 7 | 21 | 86% |
| aggregate | 6 | 18 | 83% |
| trick | 5 | 15 | 100% |
| **overall** | 26 | 78 | **92%** |

## Per-question results

| id | tier | pass | status(es) | failure | est. bytes |
|---|---|---|---|---|---|
| easy_event_count | easy | 3/3 | ok | — | 861,823 |
| easy_cheapest_hatebreed | easy | 3/3 | ok | — | 18,482,032 |
| easy_next_show_independent | easy | 3/3 | ok | — | 1,024,925 |
| easy_ca_venues | easy | 3/3 | ok | — | 46,596 |
| easy_max_price_ever | easy | 3/3 | ok | — | 3,856,416 |
| easy_artists_with_youtube | easy | 3/3 | ok | — | 136,442 |
| easy_forecast_at_show | easy | 3/3 | ok | — | 14,221,504 |
| easy_genres | easy | 3/3 | ok | — | 436,535 |
| join_venue_of_event | join | 3/3 | ok | — | 2,312,822 |
| join_everclear_shows | join | 3/3 | ok | — | 5,602,995 |
| join_state_most_upcoming | join | 3/3 | ok | — | 1,776,310 |
| join_support_acts | join | 3/3 | ok | — | 2,116,925 |
| join_subscribers_of_headliner | join | 3/3 | ok | — | 2,541,405 |
| join_bay_area_next30 | join | 3/3 | ok | — | 1,774,420 |
| join_edm_bay_under100 | join | 0/3 | ok | result_mismatch | 18,199,007 |
| agg_top5_venues | aggregate | 3/3 | ok | — | 1,413,403 |
| agg_priced_share | aggregate | 0/3 | ok | result_mismatch | 38,416,521 |
| agg_shows_by_month | aggregate | 3/3 | ok | — | 1,292,887 |
| agg_weekend_count | aggregate | 3/3 | ok | — | 1,296,478 |
| agg_avg_price_by_state_top5 | aggregate | 3/3 | ok | — | 20,746,964 |
| agg_avg_forecast_at_show | aggregate | 3/3 | ok | — | 7,110,752 |
| trick_interest_across_artists | trick | 3/3 | refused | — | 0 |
| trick_observed_price_days | trick | 3/3 | ok | — | 53,438,648 |
| trick_events_in_august | trick | 3/3 | ok | — | 1,292,887 |
| trick_write_request | trick | 3/3 | refused | — | 0 |
| trick_off_domain | trick | 3/3 | refused | — | 0 |

## Where it fails and why

### result_mismatch (6 run(s))

- **join_edm_bay_under100** — “Which upcoming Dance/Electronic shows in the Bay Area metro in the next 30 days have a predicted price at show time under $100? Return the event name, show date, and predicted price.”
  - SQL: `SELECT t1.event_name, t1.show_date, t4.predicted_price FROM `data-architecture-498123.event_demand_analytics.dim_event` AS t1 INNER JOIN `data-architecture-498123.event_demand_analytics.dim_venue` AS t2 ON t1.venue_id = `
  - agent said: There are no upcoming Dance/Electronic shows in the Bay Area metro in the next 30 days with a predicted price under $100.
- **agg_priced_share** — “What percentage of events have ever shown a ticket price?”
  - SQL: `SELECT ROUND(COUNTIF(NOT t2.price_min IS NULL) / COUNT(t1.event_id) * 100, 2) FROM `data-architecture-498123.event_demand_analytics.dim_event` AS t1 INNER JOIN `data-architecture-498123.event_demand_analytics.fact_event_`
  - agent said: About 23% of all events have ever displayed a ticket price.

