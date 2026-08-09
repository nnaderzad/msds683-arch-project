# Product decisions — the demo as a product

Small decision records for choices that shape what users *see*, distinct from
the pipeline/architecture decisions in [`architecture` / `lakehouse-plan.md`](lakehouse-plan.md).
Status is **DECIDED** (encoded in code, with the pointer) or **PROPOSED**
(needs a team call — argue in the PR or the session, then flip the status).

## PD-1 · Answer ordering defaults — DECIDED 2026-08-09

Show listings from the text-to-SQL agent default to **time order** (soonest
upcoming first) and include artist + venue names. If the user asks for
"biggest / most popular / big-name" shows, rank by **worldwide popularity**
instead (PD-2). Rationale: "what's coming up in X" is a when-question;
recency-of-opportunity is the natural default for a going-out product.
*Encoded:* semantic rule 11, `eda/build_schema_context.py`.

## PD-2 · "Popularity" means worldwide YouTube subscribers — DECIDED 2026-08-09

Big-name ranking uses the headliner's **latest `fact_youtube.official_subscribers`**.
It must never use Google Trends `interest` — that scale is normalized per pull
and cannot be compared across artists (the schema's #1 landmine). Local
interest stays what it is good at: one artist's trajectory in one metro.
*Encoded:* rule 11 + a few-shot (`build_schema_context.py`); the refusal case
is eval-tested (`trick` tier).

## PD-3 · Agent answers link back into the product — DECIDED 2026-08-09

Any answer that lists specific shows carries `event_id`, and the UI turns those
rows into click-throughs to the full dashboard view (hover = stats card).
Aggregates deliberately don't link. The agent page and dashboard page stay
separate; clicking crosses over. *Encoded:* rule 10 + `AskResultsTable.tsx`;
the id-resolves contract is pinned by `eda/qa_smoke.py` (`listing_ids_resolve`).

## PD-4 · Synthetic data is opt-in, labeled, and answers what real data can't — DECIDED (2026-08-08, restated)

The "Synthetic sandbox" toggle switches the agent to `event_demand_synth`
(simulated sellouts/resale over real events — signals no real source provides;
TM bronze has 0.0% resale data). Every synth answer is visibly labeled; the
allow-lists are disjoint so synth can never silently mix into honest answers.
Real-data demos with genuine variability are curated instead of faked:
`eda/demo_variability.py` finds upcoming shows with real price swings and
local-vs-global divergence (`eda/output/demo_variability.md`).

## PD-5 · Shows with no observed price: surface or suppress? — PROPOSED

~77% of tracked events never show a Ticketmaster price. Today they are fully
surfaced (search, agent answers, dashboard) with NULL price/forecast columns.
Options:

1. **Keep surfacing all** (today's behavior) — maximal honesty about coverage;
   the NULLs themselves demonstrate the observed-only design.
2. **Surface all, rank data-rich first** — when a question implies "what should
   I look at", order by data coverage (has price and/or forecast) before date.
3. **Hide unpriced by default** behind a "show unpriced events" toggle — the
   cleanest consumer experience, the least honest inventory view.

Leaning **2** for the product story (option 1 stays the default for anything
that reads like an inventory/coverage question), but this changes what "the
warehouse knows" looks like to a user — team call before encoding it.

## PD-6 · QA/QC layers — DECIDED 2026-08-09

Quality is layered like the guardrails, each layer catching what the previous
can't, all deterministic and committed:

| Layer | What it catches | Where |
|---|---|---|
| Offline pytest (CI, every push) | code regressions; the whole agent pipeline runs against fakes | `tests/` (300+) |
| Great Expectations gate (nightly, in-job) | bad *data* shipping — fails the gold-refresh run | `great_expectations/` |
| Eval harness (per agent change) | answer-quality regressions, by execution-result match | `eda/eval_text_to_sql.py` → committed report |
| **Live QA smoke (post-deploy / T-24h)** | the deployed surface: endpoints, guardrails live, the click-path contract (`listing event_ids resolve in /show`), docs + feedback | `eda/qa_smoke.py` |
| REPO_STATE discipline | drift between docs, deploys, and reality; incidents get written down | `docs/REPO_STATE.md` |

Run the smoke after every deploy and in the demo-runbook T-24h checklist
(`python eda/qa_smoke.py`; `--skip-llm --skip-write` for a free run).
