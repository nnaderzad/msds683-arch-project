# Handoff — presentation readiness + forecasting-model deep dive

> Untracked working note (survives the compact). Presentation is **2026-06-29**.
> Next focus (per TK): **dive into the forecasting model + the training features.**
> Budget/cost is OWNED BY TEAMMATES — not our focus. Frontend **F1–F3 is being built by a
> teammate right now** — don't touch; review after.

## Where things stand (read-only review done this session)

- **Pipeline is built and gold is populated** (live BQ `data-architecture-498123.event_demand_analytics`):
  `fact_event_demand` 609,728 rows / 9,657 events; `forecast_event_price` 494,961 rows; dims +
  `fact_trends`/`fact_trends_daily`/`fact_youtube` all present.
- **API end-product backbone is LIVE** (E2/PR #32 merged — deck `PLAN.md` calling it "stubs" is
  STALE). `api/app.py`: `/health`, `/shows` (dropdown), `/show/{event_id}` (summary + `history` +
  `forecast`). `api/gold.py` reads gold + dims, shapes JSON. Frontend will consume this.
- **Hero show curated** (`final_presentation/hero-shows.md`): **Everclear @ The Independent SF**,
  `event_id = rZ7HnEZ1Af00jd`, full coverage, 16 snapshots. Only **147** events have full
  (price+trends+youtube) coverage; only **6** are Bay-Area.
- **Forward-fill fix**: PR **#34** open, **CI green**, rebased on main. Paused at 9/19 backfill
  days; prod cutover deferred to TK (post-presentation). **Explicitly OUT of the presentation.**

## 🔴 THE critical demo risk — the forecast contradicts the real price

For the hero show: actual `price_min` = **$136.05 every day**; model forecast = **~$26** (flat).
Systemic, not a one-off — `forecast_event_price` spans only **$0.74–$92.27** while real prices go
past **$500**, so the model **can never predict a premium show**. A live "real price $136 → our
forecast $26" undercuts End-product (10 pts) + Demo quality (15 pts).

## The model + features (the deep-dive surface)

Files: `model/features.py` (D1 features), `model/train.py` (D2 train), `model/predict.py` (D2
forecast), `pipeline/gold/export_predictions_table.py` (assembles → `forecast_event_price`).

- **Model:** `HistGradientBoostingRegressor(random_state=42, categorical_features="from_dtype")`.
  Pooled cross-sectional (thin ~2-week history → pool across shows, not per-show ARIMA).
- **Target:** `price_min`. **Trained on** priced, pre-show rows only (`price_min` not null &
  `days_to_show >= 0`). Leakage guard: features never include `price_*`.
- **Features** (`FEATURE_COLUMNS`): numeric `[days_to_show, local_interest, yt_subscribers,
  yt_views, capacity]` + categorical `[genre]` + missingness flags `[has_interest, has_youtube]`.
- **Forecast generation** (`predict.py`): take each event's **latest** snapshot features, sweep
  `days_to_show` from current→0 **holding all signals constant**, floor at 0 → the "price vs
  time-to-show" curve the demo overlays.

### Why it underpredicts (root cause = feature poverty / no show-level anchor)
- The features carry **almost no show-level price-level information** — a $30 club show and a $500
  arena show can have similar interest/yt/genre. So the GBT predicts ~the conditional-MEAN price
  → **range-compressed toward ~$26–30**; premium shows always under, cheap shows over.
- **`capacity`** — the one feature that could proxy venue size / price level — is **almost always
  NULL** (TM rarely exposes it) and gets **dropped** by `train.trainable_columns` (needs ≥2
  distinct non-null values). So the model's best price anchor is effectively absent.
- The `days_to_show` effect is also tiny (hero curve ≈ 25.85→26 flat) → the demo's forecast line
  is a near-flat line at the wrong level.

### Directions to explore in the deep dive
1. **Quantify the bias** across the full-coverage pool: forecast vs actual (over/under, by price
   tier) — confirm range-compression and find any show where it looks credible for the demo.
2. **Add a show-level price anchor** — e.g., the show's **earliest observed price** as a feature
   (a *known prior observation*, predicting later price). Biggest accuracy lever; debate whether
   it's leakage (using an early KNOWN price to predict a later price is defensible — clarify).
3. **Recover `capacity`** — is it really all-null, or sourceable (venue web/seatmap)? Without a
   size proxy the model is blind to price level.
4. **Reframe vs refit for the demo** — honest options if no time to retrain: present the forecast
   as a *relative/directional demand* signal, or pick a hero show inside the model's $0–90 range,
   vs. a real feature/model change tonight (risky).
5. **Sparse signal lines (#2 from review)** — `local_interest` is only 6/16 days (snapshot grain);
   wire the demo's interest line to **`fact_trends_daily`** (daily trajectory, already loaded) for
   a smooth curve. Ties to the COLLECT-1 "rewire gold/model to fact_trends_daily" follow-on.

## Other review notes (lower priority)
- **API cold-start:** `api/gold.py` does `SELECT *` on the 609k + 495k tables on first request →
  slow/heavy cold start for a live demo; consider pre-warming or per-event queries (frontend owner).
- **Budget/cost model (5 pts):** does NOT exist — but TEAMMATES own it, not us.
- **Tech-stack justifications** already live in `team-plan.md` ("Why we can defend each choice")
  and `docs/midterm_pitch/tech_stack.md`.

## Repo/git state for next session
- Working tree on `tk/tm-forward-fill-honesty` (rebased on latest main; CI green). For
  presentation/model work, switch to latest `main` (has #32 API, #33 deck, gold-refresh job).
- Untracked: this note, `eda/diagnose_headliner_gap.py` (+`eda/output/headliner_gap_*`),
  `docs/forward_fill_fix_handoff.md`.
