# Forecast model decision — anchor + drift (2026-06-29)

How `forecast_event_price` is produced, why it changed, and how to reproduce/roll back.
Owner: TK. Model code: `model/features.py` · `model/train.py` · `model/predict.py` ·
`pipeline/gold/export_predictions_table.py`.

## Problem

The previous pooled cross-sectional model (`HistGradientBoostingRegressor`) **excluded price
from the features** (leakage guard) and learned price as a function of `days_to_show` +
exogenous demand signals only. With no per-show price-level signal it regressed toward the
demand-driven conditional mean (~$23), so it **range-compressed**: right for cheap club shows,
but it underpriced the entire premium tail.

## Evidence (deterministic, committed)

**1. Prices barely move — it's a LEVEL problem.** `eda/diagnose_price_movement.py`
(→ `eda/output/price_movement.md`):

- **95.6%** of priced shows are **perfectly flat** across their window (honest
  `tm_observations`); 91.6% on the forward-filled gold. Only ~4% ever change price.
- Cross-show price **level** spans $0 → $318 (honest) / **$569** (gold); p50 $25, p90 $46,
  p99 $92. So the spread is *across* shows, not *within* a show.

→ A per-show ARIMA has nothing to fit (no within-show dynamics); the pooled-mean model
throws away the one thing that matters (each show's own level).

**2. Old vs new bias.** `eda/diagnose_forecast_bias.py` (→ `eda/output/forecast_bias.md`),
scored forecast-at-show-date vs actual latest price by tier:

| tier (by actual) | n | OLD median fcst vs actual | NEW median fcst vs actual |
|---|--:|---|---|
| <$30 | 6,478 | $22 vs $19 | $18 vs $19 |
| $30–75 | 3,260 | **$23 vs $38** | $36 vs $38 |
| $75–150 | 133 | **$23 vs $95** | $94 vs $95 |
| $150+ | 16 | **$22 vs $219** | $218 vs $219 |

Overall MAE $12.85 → **$1.90**; premium-tail (>$90) MAE **$98 → $5**; forecast range
$2.92–$161.81 → **$0–$528** (actuals reach $569).

## Decision

**Anchor + drift (delta) model.**
- `features.build_training_frame` computes a per-show **anchor** = the show's **earliest
  observed price** (a known *past* observation → no future leakage; it is listed in
  `PRICE_COLUMNS` so it can never enter the feature matrix). The model target is
  `price_delta = price_min − anchor_price`.
- `predict.predict_prices` rebuilds the level by **anchoring on the latest real price** and
  adding *re-centered* drift: `predicted(d) = latest_real_price + drift(d) − drift(today)`,
  so the curve passes exactly through the show's last real price and only the model's
  relative drift moves it. The level comes from a real price, not the tree output, so it
  **cannot range-compress** and reaches any real price level.
- Engine unchanged (`HistGradientBoostingRegressor`, seed 42, deterministic). XGBoost
  bake-off deferred until the data/features are cleaned.

**Drift kept at full strength (user decision).** The exogenous signals genuinely drive the
forecast trajectory. Caveat: because most shows are flat, projecting pooled drift over a long
horizon can overshoot — e.g. **Everclear (`rZ7HnEZ1Af00jd`) forecasts ~$174 over a flat $136
history** (118-day horizon). The bias metric is also near-circular for flat data (the "actual"
is the anchor), so it cannot *validate* drift — only the level is validatable, and the anchor
gets it exactly. Alternatives considered: drift=0 (pure carry-forward; hero exact) and damped
drift. Full drift was chosen to keep signals meaningful.

**Hero selection.** `eda/hero_candidates.py` → `final_presentation/hero-candidates.{md,csv}`
classifies every full-coverage show by drift (`flat`/`rising`/`falling`/`steep`). All 6
Bay-Area shows are weak under full drift; prefer nationwide **flat** shows for the live hero
(Shakey Graves, Air Supply, Masego, **Madeon** = electronic).

## Reproduce / roll back

```bash
# rebuild (deterministic) and re-export to live (WRITE_TRUNCATE):
conda run -n music-demand python pipeline/gold/export_predictions_table.py
# verify:
conda run -n music-demand python eda/diagnose_forecast_bias.py --sources live
# roll back to the pre-2026-06-29 model:
bq cp -f data-architecture-498123:event_demand_analytics.forecast_event_price_bak_20260629 \
         data-architecture-498123:event_demand_analytics.forecast_event_price
```

The old (pre-change) table is preserved at `forecast_event_price_bak_20260629` (499,768 rows).
