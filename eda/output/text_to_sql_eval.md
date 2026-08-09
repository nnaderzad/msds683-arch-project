# Text-to-SQL agent — evaluation report

Generated 2026-08-09T09:22:35+00:00 by `eda/eval_text_to_sql.py --runs 3` (model: gemini-2.5-flash).
Scoring: execution-result match against committed gold SQL (values-only multiset;
refusal questions pass on refused/blocked). Re-run the same command to refresh.

## Accuracy

| Tier | Questions | Runs | Accuracy |
|---|---|---|---|
| easy | 9 | 27 | 89% |
| join | 10 | 30 | 100% |
| aggregate | 7 | 21 | 100% |
| trick | 6 | 18 | 100% |
| **overall** | 32 | 96 | **97%** |

## Per-question results

| id | tier | pass | status(es) | failure | est. bytes |
|---|---|---|---|---|---|
| easy_event_count | easy | 3/3 | ok | — | 0 |
| easy_cheapest_hatebreed | easy | 3/3 | ok | — | 0 |
| easy_next_show_independent | easy | 3/3 | ok | — | 1,024,925 |
| easy_ca_venues | easy | 3/3 | ok | — | 0 |
| easy_max_price_ever | easy | 3/3 | ok | — | 0 |
| easy_artists_with_youtube | easy | 3/3 | ok | — | 0 |
| easy_forecast_at_show | easy | 3/3 | ok | — | 0 |
| easy_genres | easy | 3/3 | ok | — | 0 |
| join_venue_of_event | join | 3/3 | ok | — | 0 |
| join_everclear_shows | join | 3/3 | ok | — | 5,602,995 |
| join_state_most_upcoming | join | 3/3 | ok | — | 1,770,547 |
| join_support_acts | join | 3/3 | ok | — | 0 |
| join_subscribers_of_headliner | join | 3/3 | ok | — | 0 |
| join_bay_area_next30 | join | 3/3 | ok | — | 1,774,420 |
| join_edm_bay_under100 | join | 3/3 | ok | — | 18,193,701 |
| agg_top5_venues | aggregate | 3/3 | ok | — | 0 |
| agg_priced_share | aggregate | 3/3 | ok | — | 37,554,698 |
| agg_shows_by_month | aggregate | 3/3 | ok | — | 1,292,887 |
| agg_weekend_count | aggregate | 3/3 | ok | — | 0 |
| agg_avg_price_by_state_top5 | aggregate | 3/3 | ok | — | 20,746,964 |
| agg_avg_forecast_at_show | aggregate | 3/3 | ok | — | 0 |
| trick_interest_across_artists | trick | 3/3 | refused | — | 0 |
| trick_observed_price_days | trick | 3/3 | ok | — | 0 |
| trick_events_in_august | trick | 3/3 | ok | — | 0 |
| trick_write_request | trick | 3/3 | refused | — | 0 |
| trick_off_domain | trick | 3/3 | refused | — | 0 |
| vocab_bay_area_2026 | join | 3/3 | ok | — | 1,774,420 |
| vocab_edm_bay_area | join | 3/3 | ok | — | 2,210,955 |
| vocab_hiphop_alias | easy | 0/3 | ok | result_mismatch | 3,248,679 |
| vocab_jazz_vegas | join | 3/3 | ok | — | 2,210,955 |
| vocab_busiest_bay_venue | aggregate | 3/3 | ok | — | 1,863,872 |
| trick_leadlag_answerable | trick | 3/3 | ok | — | 0 |

## Where it fails and why

### result_mismatch (3 run(s))

- **vocab_hiphop_alias** — “How many upcoming hip hop shows are we tracking nationwide?”
  - SQL: `SELECT COUNT(DISTINCT e.event_id) FROM `data-architecture-498123.event_demand_analytics.dim_event` AS e JOIN `data-architecture-498123.event_demand_analytics.bridge_event_artist` AS ba ON e.event_id = ba.event_id JOIN `d`
  - agent said: There are 1,935 upcoming hip-hop shows being tracked nationwide.

