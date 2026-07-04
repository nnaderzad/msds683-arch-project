# Handoff — deploy the demo (public UI + API) + hero-dropdown polish

> Untracked working note. Survives `/compact`. Presentation is **2026-06-29** (tomorrow).
> After compacting, read **§0** first, then execute **Parts A→D**.

## 0. Session state (read first)

**Already shipped this session (forecast-model work — DONE):**
- `model/` reworked to **anchor + drift** (forecasts start at each show's real price, span
  $0–$528). Premium-tail MAE $98→$5; per-tier forecast tracks actual. 8 unit tests green.
- **Live `forecast_event_price` re-exported** = new model (499,768 rows / 9,887 events).
  Old table backed up at **`forecast_event_price_bak_20260629`** (rollback: `bq cp -f`).
  `fact_event_demand` unchanged.
- **PR #39 OPEN, CI GREEN** — branch `tk/price-forecast-anchor`, rebased on current `main`
  (5 commits: 2 eda diagnostics, model change, hero-candidates, docs+team-plan).
- Evidence: `eda/diagnose_price_movement.py` (96% of shows flat), `eda/diagnose_forecast_bias.py`
  (OLD vs NEW), `eda/hero_candidates.py` → `final_presentation/hero-candidates.{md,csv}`.
  Decision record: `docs/forecast_model_decision.md`. Memory: `forecast-anchor-model.md`.

**Code review of the merged UI/infra PRs ("how do they look"): all SOLID.**
- #38 F3 API wiring, #37 F2 chart hardening, #36 G1 gsutil fix, + "Align chart prices with
  forecast target" / "Render forecast as dashed line" — clean, tested.
- **The merged chart already handles the anchor forecast** (Y-axis auto-scales over
  history+forecast w/ 25% padding; forecast dashed; price axis aligned to `price_min` = our
  anchor). **No chart code changes needed.** Minor non-blockers: `web/src/api/client.ts`
  doesn't catch malformed JSON; tests don't exercise the forecast-above-history case.

**Environment / infra facts (verified this session):**
- Python env with BigQuery/sklearn = **`music-demand`** conda env (base anaconda lacks
  google-cloud-bigquery). Use `/opt/anaconda3/envs/music-demand/bin/python`.
- **gcloud is pointed at the WRONG project** (`retaining-waller`) → must `gcloud config set
  project data-architecture-498123`. Region `us-west1`. APIs (run/build/artifactregistry)
  enabled. Billing active (on the MSDS691 account).
- **Terraform state is NOT on this machine** (a teammate applied the live infra) → **gcloud
  deploy only; do NOT `terraform apply` here.**
- Web app: Vite+React19+TS+recharts in `web/`; API base via `VITE_API_BASE_URL`
  (default `127.0.0.1:8000`, `web/src/api/client.ts`). FastAPI in `api/` has a Dockerfile
  (uvicorn :8080), reads gold read-only via `DBT_GCP_PROJECT`/`DBT_BQ_DATASET` (defaults
  already correct). `api/gold.py` does `SELECT *` on ~1.2M rows on first request
  (cold-start → `--min-instances 1` + optional startup pre-warm).
- Today's hardcoded dropdown default = Everclear (now a `steep` overshoot). **Chosen new
  default = Madeon `rZ7HnEZ1Af0Z4K`** (electronic/on-thesis, flat $41 forecast). Most-popular
  by subs was Air Supply (2.11M), but the user picked Madeon.

**Decisions locked with the user:**
- Deploy via **`gcloud run deploy` now** (state-safe); **codify in terraform after** for the
  state-owner to adopt. Do not `terraform apply` from this machine.
- **Serve the web FROM the API** = one Cloud Run service, one public URL, no CORS.
- Default-selected show = **Madeon** `rZ7HnEZ1Af0Z4K`.

**Unrelated open work (don't touch):** PR #34 (forward-fill honesty) is paused/deferred.

---

## Part A — Hero dropdown + default (web/)

**A1. Generate a curated hero artifact (deterministic).** Extend `eda/hero_candidates.py` to
also emit `web/src/data/heroShows.ts` exporting:
- `HERO_EVENT_IDS: string[]` — credible heroes (`showcase ∈ {flat,rising,falling}`, **deduped
  per artist**, ranked by `yt_subs` desc; ~top 12).
- `DEFAULT_HERO_EVENT_ID = "rZ7HnEZ1Af0Z4K"` (Madeon).
Single source of truth; regenerate by re-running the script. (Dedup-per-artist logic already
prototyped this session.)

**A2. `web/src/App.tsx`** — replace the inline `HERO_EVENT_IDS` (lines 10–17, the 6 weak
Bay-Area ids) with the import from `./data/heroShows`. Keep `showScore`/`prepareDropdownShows`
(heroes float to top). Change the default at **line 87** to prefer the default hero:
`setSelectedId(cur => cur || (dropdownShows.some(s => s.event_id === DEFAULT_HERO_EVENT_ID) ? DEFAULT_HERO_EVENT_ID : dropdownShows[0]?.event_id) || "")`.

**A3. `web/src/App.test.tsx`** — the suite asserts the default selected value (≈ line 129,
`"event_soon"`). Add `DEFAULT_HERO_EVENT_ID` to the mock `/shows` payload and assert it is the
default; keep the user-change-selection test.

**A4. Verify:** `cd web && npm ci && npm run test && npm run build`.

---

## Part B — Serve web from the API (one container, same-origin)

**B1. `web/src/api/client.ts`** — make prod default to same-origin. In `apiBaseUrl()`, when
`VITE_API_BASE_URL` is unset: return `""` (relative) if `import.meta.env.PROD`, else keep
`http://127.0.0.1:8000` for local dev. Preserve explicit override.

**B2. `api/app.py`** — mount the built SPA AFTER the API routes so `/health`,`/shows`,
`/show/{id}` win:
```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path
_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
```
Optional cold-start pre-warm: an `@app.on_event("startup")` that calls `get_repository()` so the
first user request is instant (pairs with `--min-instances 1`).

**B3. `api/Dockerfile`** — multi-stage (build context = repo root):
```dockerfile
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build            # prod build → same-origin API via B1

FROM python:3.12-slim
WORKDIR /app
COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r /app/api/requirements.txt
COPY api/ /app/api/
COPY --from=web /web/dist /app/api/static
EXPOSE 8080
CMD ["uvicorn","api.app:app","--host","0.0.0.0","--port","8080"]
```

**B4.** Verify `api/requirements.txt` includes `fastapi`, `uvicorn`, `google-cloud-bigquery`,
`pandas`, and `db-dtypes`/`pyarrow` (needed by `to_dataframe()`); add if missing.

---

## Part C — Deploy (gcloud, state-safe)

**C1. Prereqs (user runs/authorizes):**
```bash
gcloud config set project data-architecture-498123
gcloud auth login                      # if needed
gcloud auth application-default set-quota-project data-architecture-498123
gcloud beta billing projects describe data-architecture-498123   # confirm billing active
```
Tomas's account needs run.admin, cloudbuild.builds.editor, artifactregistry.admin, iam SA
admin/user (he set the project up → likely owner; confirm).

**C2. Read-only service account:**
```bash
gcloud iam service-accounts create event-demand-api --display-name "Event Demand API"
SA=event-demand-api@data-architecture-498123.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding data-architecture-498123 \
  --member="serviceAccount:$SA" --role="roles/bigquery.jobUser"
# dataViewer on the dataset (Console, or):
bq update --dataset --source <(bq show --format=prettyjson data-architecture-498123:event_demand_analytics) ...
# simplest: grant dataViewer via Console on the event_demand_analytics dataset to $SA
```

**C3. Build the image** (Cloud Build, repo-root context, `-f api/Dockerfile`). Create
`api/cloudbuild.yaml` (docker build -f api/Dockerfile -t $_IMAGE . → push):
```bash
gcloud artifacts repositories create services --repository-format=docker --location=us-west1 || true
IMAGE=us-west1-docker.pkg.dev/data-architecture-498123/services/event-demand-api:$(git rev-parse --short HEAD)
gcloud builds submit --config api/cloudbuild.yaml --substitutions=_IMAGE=$IMAGE .
```

**C4. Deploy the service** (public, warm):
```bash
gcloud run deploy event-demand-api --image $IMAGE --region us-west1 \
  --service-account $SA --allow-unauthenticated \
  --min-instances 1 --memory 1Gi --cpu 1 \
  --set-env-vars DBT_GCP_PROJECT=data-architecture-498123,DBT_BQ_DATASET=event_demand_analytics
URL=$(gcloud run services describe event-demand-api --region us-west1 --format='value(status.url)')
```
`$URL` is the single public UI+API endpoint (web served same-origin).

**C5. Codify-after (follow-up PR, NOT applied here):** commit `terraform/api_service.tf`
(Cloud Run v2 service + read-only SA + allUsers invoker + min-instances, mirroring
`terraform/gold_refresh_job.tf`) and `api/cloudbuild.yaml`, for the terraform state-owner.

---

## Part D — Verify end-to-end
- `curl $URL/health` → ok; `curl $URL/shows` → non-empty;
  `curl $URL/show/rZ7HnEZ1Af0Z4K` → Madeon summary + `history` + `forecast`.
- Open `$URL` → dashboard loads, **Madeon default-selected**, dropdown shows curated heroes,
  chart renders price history + dashed forecast (~$41, tracks the real price).
- First load fast (min-instances 1 + pre-warm); no cold-start stall.
- `web` vitest + build green; `ruff`/`pytest` unaffected by the small `api/app.py` change.

## Caveats
- **Public unauthenticated** API hitting BigQuery is fine for a demo (public event data, tiny,
  warm single instance). **After the demo:** `gcloud run services delete event-demand-api` or
  set `--min-instances 0` to avoid idle cost.
- **Do not `terraform apply` from this machine** (no state) — gcloud only; terraform is codify-after.
- Frontend chart already handles the anchor forecast — no chart changes.

## Files
- **Modify:** `web/src/App.tsx`, `web/src/App.test.tsx`, `web/src/api/client.ts`,
  `api/app.py`, `api/Dockerfile`, `eda/hero_candidates.py` (emit heroShows.ts).
- **Create:** `web/src/data/heroShows.ts` (generated), `api/cloudbuild.yaml`,
  `terraform/api_service.tf` (codify-after, not applied).
- **Verify:** `api/requirements.txt` (db-dtypes/pyarrow).

## Suggested commits / PRs
1. UI hero dropdown + Madeon default (web/ + generator) — one small PR.
2. Serve-web-from-API (api/app.py + Dockerfile + client.ts).
3. Operational deploy (gcloud).
4. `terraform/api_service.tf` codify-after — small PR for the state-owner.
