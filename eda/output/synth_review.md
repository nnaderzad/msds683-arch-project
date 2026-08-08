# Synthetic layer QC review

Generated 2026-08-08T20:33:09+00:00 by `eda/synth_review.py`. Source: `event_demand_synth.synth_event_demand` / `synth_resale_series` (hybrid design: real event spine, synthetic sellout/resale infill — see synth/heuristics.py for the encoded market stories).

## Provenance & labeling

| synth_run_id | generator_version | seed | events | researched_capacity | observed_face | genre_median_face |
|---|---|---|---|---|---|---|
| seed683-v1.0.0 | 1.0.0 | 683 | 53861 | 15485 | 11152 | 40378 |

## Sellout + resale by demand band (must be monotone)

| demand_band | events | pct_sold_out | median_resale_mult |
|---|---|---|---|
| a: <0.5 (soft) | 48895 | 2.9 | 0.53 |
| b: 0.5-1.0 | 3410 | 22.3 | 0.83 |
| c: 1.0-2.0 (hot) | 1101 | 74.9 | 1.25 |
| d: >=2.0 (oversubscribed) | 455 | 99.8 | 2.49 |

## By event type

| event_type | events | pct_sold_out | median_ratio |
|---|---|---|---|
| concert | 40854 | 5.9 | 0.015 |
| club | 12280 | 8.3 | 0.12 |
| festival | 727 | 2.3 | 0.002 |

## Anecdote regimes (the heuristics' ground truth)

| regime | events | pct_sold_out | median_resale_mult |
|---|---|---|---|
| in-demand act, small room (MGMT regime) | 342 | 96.8 | 2.02 |
| soft big room (fire-sale regime) | 1745 | 2.1 | 0.51 |

## Synth face price vs real observed median, by genre

| primary_genre | synth_events | synth_median_face | real_median_price |
|---|---|---|---|
| Rock | 14402 | 24.91 | 24.91 |
| Other | 7119 | 21.09 | 21.09 |
| Country | 4693 | 22.56 | 22.56 |
| Pop | 4510 | 26.16 | 26.16 |
| Alternative | 3132 | 22.73 | 22.73 |
| Hip-Hop/Rap | 2829 | 25.21 | 25.21 |
| Dance/Electronic | 2362 | 21.18 | 21.18 |
| Jazz | 2316 | 35.46 | 35.46 |
| R&B | 2100 | 24.65 | 24.65 |
| Undefined | 2059 | 11.8 | 11.8 |
| Metal | 1619 | 25.43 | 25.43 |
| Folk | 1139 | 22.35 | 22.35 |

## Resale trajectory (median multiplier by days-to-show)

| days_to_show | median_mult |
|---|---|
| 45 | 1.0 |
| 30 | 0.911 |
| 21 | 0.821 |
| 14 | 0.737 |
| 7 | 0.643 |
| 3 | 0.585 |
| 1 | 0.555 |
| 0 | 0.54 |
