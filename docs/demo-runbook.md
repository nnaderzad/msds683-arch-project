# Final-presentation demo runbook (Mon 2026-08-11)

Live URL: **https://event-demand-api-mqd3drcneq-uw.a.run.app**
Time budget: 7 min talk + 5 min Q&A. The demo slots below fit the instructor's
section timings (live end-to-end ~1 min, deep dive ~2.5 min).

## T-24h (Sunday evening)

- [ ] Confirm the last two scheduled `gold-refresh` executions SUCCEEDED
      (`gcloud run jobs executions list --job=gold-refresh --region=us-west1 --limit=3`)
      — execution status, not freshness (July's lesson).
- [ ] Freshness SQL (REPO_STATE §Data freshness): every fact ≥ yesterday.
- [ ] Service deployed with `--min-instances 1` (kills the 152 s cold start) and
      the current image (AskPanel + SearchPanel + fresh heroes).
- [ ] `eda/output/text_to_sql_eval.md` regenerated on the fresh warehouse; the
      accuracy table screenshotted into the deck.
- [ ] **`python eda/qa_smoke.py`** — 14 deterministic live checks (endpoints,
      guardrails, the answer-row click-path contract). All must PASS.
- [ ] Pick 1–2 variability shows from `eda/output/demo_variability.md`
      (re-run `python eda/demo_variability.py` for fresh candidates) to show a
      price chart that actually moves.
- [ ] Timed dry run of the full click path below (target: under 4 min total).

## T-30min (before class)

- [ ] Open the live URL in a tab; click one hero show (warms nothing — instance
      is warm — but proves it's up).
- [ ] Ask one throwaway question in the AskPanel (confirms Vertex path is live).
- [ ] Backup tabs open: `<url>/docs` (Swagger — the fallback if the React UI
      misbehaves) and the committed eval report + benchmark md on GitHub.

## Click path

**1. Live end-to-end (~1 min)** — dashboard view:
- Hero dropdown → pick the default hero: price history + Trends + YouTube +
  forecast on one chart. One sentence: "collected this morning by the scheduled
  jobs, bronze → silver → dbt gold → this chart, all on GCP."
- SearchPanel: genre `Dance/Electronic` + **Bay Area only** + max $100 +
  next 30 days → Search → click **View** on a result → its chart loads.

**2. Deep dive: text-to-SQL (~2.5 min)** — Ask view ("Ask the music warehouse"):
- Chip 1 (easy): *cheapest Everclear ticket* → point at the **generated SQL**,
  the green guardrail badges, and the bytes-scanned footer.
- Chip 2 (compound): *upcoming EDM in the Bay Area under $100* — same question
  the manual search just answered, now via natural language. **Hover a result
  row** (stats card pops) → **click it** — the dashboard opens on that show:
  "answers link straight back into the product."
- Follow-up beat: ask *"How many of those are at The Midway?"* — the agent
  resolves "those" from conversation history (multi-turn).
- Guardrail beats (fast): type *"Drop the fact_event_demand table"* → refusal;
  type *"Which artist has the highest average Trends interest across metros?"*
  → semantic refusal (per-pull normalization — schema-design story in one line).
- Tap **👍** on a good answer — one sentence: votes stream into a separate ops
  dataset and get mined into the eval set (the agent itself stays read-only).
- Optional if time: toggle **Synthetic sandbox** → *"Which sold-out shows have
  the highest resale markup?"* → purple SYNTHETIC badge; one sentence on the
  hybrid synth design (real spine, simulated resale, separate dataset).
- Close with the eval slide: accuracy by tier + the failure taxonomy — and the
  live-failure story: a real user question ("EDM shows in SF") failed on an
  invented genre literal; canonical vocabularies + a zero-row retry fixed it;
  the set grew 26→31 questions.

**2b. If asked "how do we know what it's built on"** — header "How it works"
view renders the committed architecture/data-model/REPO_STATE docs, bundled
into the image each deploy — the docs can't drift from the running system.

**3. Benchmark slide (~1 min)** — from `eda/output/`:
- `benchmark_trends_window.md`: 47→60 min & 27 nightly timeouts → minutes
  (the optimization that un-broke the pipeline), 5,049→1,275 objects read.
- `benchmark_partitioning.md`: single-day query −96% bytes; clustered event
  lookup −79% (only visible in ACTUAL bytes — dry-run can't see cluster pruning).

## Failure fallbacks

| Symptom | Fallback |
|---|---|
| React UI broken | `<url>/docs` Swagger: POST /ask with the same questions — JSON shows sql/guardrails/answer |
| Vertex/LLM outage | Committed eval report has real Q→SQL→result examples; walk through those |
| BigQuery slow | `truncated`/bytes caps mean queries stay small; retry once, else Swagger + eval report |
| Wifi dies | Screenshots in the deck of: dashboard, one /ask response with SQL+badges, eval table, benchmark tables |

## Post-demo revert list (Monday night)

- [ ] `gcloud run services update event-demand-api --region us-west1 --min-instances 0`
- [ ] `python eda/benchmark_partitioning.py --cleanup` (drop the 3.98 GiB twins)
      — after the blog's numbers are final.
- [ ] Rate limits / budget alert stay (they cost nothing).
