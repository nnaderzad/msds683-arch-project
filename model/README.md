# model/ — anchor+drift price forecaster

Precomputes each upcoming show's price trajectory (today → show date) for the
gold `forecast_event_price` table. Full decision record, evidence, and rollback:
[`../docs/forecast_model_decision.md`](../docs/forecast_model_decision.md).

## The idea

Measured fact (`eda/diagnose_price_movement.py`): **~96% of shows never move
price** across their observed window — this is a price *level* problem, not a
drift problem. A pooled model that predicts the level regresses toward the mean
and range-compresses (premium shows become unreachable). So instead:

- the model learns only `price_delta` — the small deviation from each show's
  own anchor price — as a function of `days_to_show` + demand signals;
- at predict time the curve is **anchored on the show's latest observed real
  price** and only the model's *re-centered* relative drift bends it:
  `predicted(d) = latest_real_price + drift(d) − drift(today)`.

The forecast therefore passes exactly through each show's last real price
(premium-tier MAE went $98 → $5) and can reach any real price level.

## Files

| File | Role |
|---|---|
| `features.py` | training frame from gold + dims; leakage guard (price columns never enter X); missingness flags so "signal not collected" is itself a feature |
| `train.py` | `HistGradientBoostingRegressor`, fixed seed 42 — handles NaN and `genre` natively, deterministic and re-runnable |
| `predict.py` | expands each event into a per-day curve, anchors + re-centers the drift, floors at $0 |

The entrypoint that runs all three against BigQuery and writes the table is
[`../pipeline/gold/export_predictions_table.py`](../pipeline/gold/export_predictions_table.py)
(the `forecast_export` step of the nightly gold-refresh job).

## Run

```bash
conda activate music-demand
pip install -r model/requirements.txt
gcloud auth application-default login

python pipeline/gold/export_predictions_table.py --dry-run   # assemble + report, write nothing
python pipeline/gold/export_predictions_table.py             # write forecast_event_price
```

Offline tests: `tests/test_features.py`, `tests/test_train_predict.py`.
Q&A notes: signals are held at their latest value, not projected forward
(projecting them would compound error on our thin history); heavier models were
rejected — at a 96%-flat base rate there is no drift signal to learn.
