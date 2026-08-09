# Text-to-SQL agent — evaluation report

Generated 2026-08-09T06:25:16+00:00 by `eda/eval_text_to_sql.py --runs 3` (model: gemini-2.5-flash).
Scoring: execution-result match against committed gold SQL (values-only multiset;
refusal questions pass on refused/blocked). Re-run the same command to refresh.

## Accuracy

| Tier | Questions | Runs | Accuracy |
|---|---|---|---|
| easy | 8 | 24 | 100% |
| join | 7 | 21 | 100% |
| aggregate | 6 | 18 | 100% |
| trick | 5 | 15 | 100% |
| **overall** | 26 | 78 | **100%** |

## Per-question results

| id | tier | pass | status(es) | failure | est. bytes |
|---|---|---|---|---|---|
| easy_event_count | easy | 3/3 | ok | — | 0 |
| easy_cheapest_hatebreed | easy | 3/3 | ok | — | 18,482,032 |
| easy_next_show_independent | easy | 3/3 | ok | — | 1,024,925 |
| easy_ca_venues | easy | 3/3 | ok | — | 0 |
| easy_max_price_ever | easy | 3/3 | ok | — | 0 |
| easy_artists_with_youtube | easy | 3/3 | ok | — | 0 |
| easy_forecast_at_show | easy | 3/3 | ok | — | 0 |
| easy_genres | easy | 3/3 | ok | — | 0 |
| join_venue_of_event | join | 3/3 | ok | — | 0 |
| join_everclear_shows | join | 3/3 | ok | — | 5,602,995 |
| join_state_most_upcoming | join | 3/3 | ok | — | 1,770,547 |
| join_support_acts | join | 3/3 | ok | — | 2,116,925 |
| join_subscribers_of_headliner | join | 3/3 | ok | — | 0 |
| join_bay_area_next30 | join | 3/3 | ok | — | 1,774,420 |
| join_edm_bay_under100 | join | 3/3 | ok | — | 18,193,701 |
| agg_top5_venues | aggregate | 3/3 | ok | — | 0 |
| agg_priced_share | aggregate | 3/3 | ok | — | 0 |
| agg_shows_by_month | aggregate | 3/3 | ok | — | 0 |
| agg_weekend_count | aggregate | 3/3 | ok | — | 0 |
| agg_avg_price_by_state_top5 | aggregate | 3/3 | ok | — | 0 |
| agg_avg_forecast_at_show | aggregate | 3/3 | ok | — | 0 |
| trick_interest_across_artists | trick | 3/3 | refused | — | 0 |
| trick_observed_price_days | trick | 3/3 | ok | — | 0 |
| trick_events_in_august | trick | 3/3 | ok | — | 0 |
| trick_write_request | trick | 3/3 | refused | — | 0 |
| trick_off_domain | trick | 3/3 | refused | — | 0 |

## Where it fails and why

No failures in this run.
