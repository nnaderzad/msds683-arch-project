# Text-to-SQL agent — evaluation report

Generated 2026-08-10T19:00:14+00:00 by `eda/eval_text_to_sql.py --runs 3` (model: gemini-2.5-flash).
Scoring: execution-result match against committed gold SQL (values-only multiset;
refusal questions pass on refused/blocked). Re-run the same command to refresh.

## Accuracy

| Tier | Questions | Runs | Accuracy |
|---|---|---|---|
| easy | 9 | 27 | 100% |
| join | 10 | 30 | 100% |
| aggregate | 7 | 21 | 86% |
| trick | 6 | 18 | 100% |
| **overall** | 32 | 96 | **97%** |

## Per-question results

| id | tier | pass | status(es) | failure | est. bytes |
|---|---|---|---|---|---|
| easy_event_count | easy | 3/3 | ok | — | 862,536 |
| easy_cheapest_hatebreed | easy | 3/3 | ok | — | 18,786,832 |
| easy_next_show_independent | easy | 3/3 | ok | — | 1,025,672 |
| easy_ca_venues | easy | 3/3 | ok | — | 46,608 |
| easy_max_price_ever | easy | 3/3 | ok | — | 3,919,744 |
| easy_artists_with_youtube | easy | 3/3 | ok | — | 136,610 |
| easy_forecast_at_show | easy | 3/3 | ok | — | 14,006,496 |
| easy_genres | easy | 3/3 | ok | — | 436,944 |
| join_venue_of_event | join | 3/3 | ok | — | 2,314,858 |
| join_everclear_shows | join | 3/3 | ok | — | 5,607,047 |
| join_state_most_upcoming | join | 3/3 | ok | — | 909,456 |
| join_support_acts | join | 3/3 | ok | — | 2,117,899 |
| join_subscribers_of_headliner | join | 3/3 | ok | — | 2,558,251 |
| join_bay_area_next30 | join | 3/3 | ok | — | 1,775,866 |
| join_edm_bay_under100 | join | 3/3 | ok | — | 17,982,207 |
| agg_top5_venues | aggregate | 3/3 | ok | — | 1,414,493 |
| agg_priced_share | aggregate | 0/3 | ok | result_mismatch | 38,175,566 |
| agg_shows_by_month | aggregate | 3/3 | ok | — | 1,293,960 |
| agg_weekend_count | aggregate | 3/3 | ok | — | 1,297,569 |
| agg_avg_price_by_state_top5 | aggregate | 3/3 | ok | — | 21,090,296 |
| agg_avg_forecast_at_show | aggregate | 3/3 | ok | — | 7,003,248 |
| trick_interest_across_artists | trick | 3/3 | refused | — | 0 |
| trick_observed_price_days | trick | 3/3 | ok | — | 54,244,161 |
| trick_events_in_august | trick | 3/3 | ok | — | 1,293,960 |
| trick_write_request | trick | 3/3 | refused | — | 0 |
| trick_off_domain | trick | 3/3 | refused | — | 0 |
| vocab_bay_area_2026 | join | 3/3 | ok | — | 1,775,866 |
| vocab_edm_bay_area | join | 3/3 | ok | — | 2,212,810 |
| vocab_hiphop_alias | easy | 3/3 | ok | — | 1,730,904 |
| vocab_jazz_vegas | join | 3/3 | ok | — | 2,212,810 |
| vocab_busiest_bay_venue | aggregate | 3/3 | ok | — | 1,865,327 |
| trick_leadlag_answerable | trick | 3/3 | ok | — | 66,546,726 |

## Where it fails and why

### result_mismatch (3 run(s))

- **agg_priced_share** — “What percentage of events have ever shown a ticket price?”
  - SQL: `SELECT ROUND(COUNTIF(NOT f.price_min IS NULL) / COUNT(f.event_id) * 100, 2) FROM `data-architecture-498123.event_demand_analytics.fact_event_demand` AS f LIMIT 200`
  - agent said: About 23% of events have ever shown a ticket price.

