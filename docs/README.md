# docs/ — documentation index

**Read [`REPO_STATE.md`](REPO_STATE.md) first.** It is the living "where things
stand" doc — live system map, schedules, data freshness, incident log — and the
standing rule is to update it in every PR that changes pipeline behavior,
deploys anything, or moves data coverage.

## Working docs

| Doc | What |
|---|---|
| [`lakehouse-plan.md`](lakehouse-plan.md) | the current task board: lakehouse-class roadmap (text-to-SQL agent, synthetic layer, benchmarks, demo, blog) |
| [`data-model.md`](data-model.md) | the locked schema — silver constellation + gold star (Mermaid, renders on GitHub) |
| [`transformations_showcase.md`](transformations_showcase.md) | stage-by-stage pipeline walkthrough: every transform with sample input/output schemas + SQL |

Generated from `data-model.md` (do not hand-edit):
[`data-model-uml.md`](data-model-uml.md), [`mermaid/`](mermaid/), and
`data-model.drawio` via `build_drawio.py`; rendered PNGs in `img/`.

## Decision records

| Doc | Decision |
|---|---|
| [`product-decisions.md`](product-decisions.md) | what users see: answer ordering, popularity definition, clickable answers, synth stance, the unpriced-shows question (open), QA/QC layers |
| [`forecast_model_decision.md`](forecast_model_decision.md) | the anchor+drift forecast rework (2026-06-29): problem, evidence, rollback |
| [`collection_efficiency_review.md`](collection_efficiency_review.md) | the 2026-07 collection review: TM 2×/day, Trends budget, 19hz/RA adoption (findings 1–12, decisions D1–D8) |
| [`artist_links_enrichment.md`](artist_links_enrichment.md) | proposal (no code yet): artist external-links enrichment sizing + handoff |
| [`ra_access_request.md`](ra_access_request.md) · [`tm_access_request.md`](tm_access_request.md) | source-access requests + status (RA: written 1-request/day permission) |
| [`post_show_cutoff_todo.md`](post_show_cutoff_todo.md) | deferred server-side post-show cutoff notes |

## History

- [`data-arch-project-plan(old).md`](<data-arch-project-plan(old).md>) ·
  [`data-arch-team-plan(old).md`](<data-arch-team-plan(old).md>) — the original
  MSDS 683 plans, superseded by REPO_STATE + lakehouse-plan
- [`midterm_pitch/`](midterm_pitch/) — midterm deliverables: schema decision,
  tech stack, transformation pipeline
- [`archive/`](archive/) — completed handoffs (forward-fill fix, UI deploy,
  presentation readiness)
- `shakshuka-*` — the original whiteboard domain/data-model draft that
  `data-model.md` cleaned up

Some personal working notes in this directory (e.g. `PROJECT_STRATEGY.md`,
`team_messages.md`) are gitignored/local-only — don't link them from committed
docs.
