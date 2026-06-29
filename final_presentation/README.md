# Final presentation — working folder

Planning + demo materials for the MSDS683 final presentation (~10 min, due 2026-06-29).
Skeleton/first-draft — refine with the team.

## Contents
- **[PLAN.md](PLAN.md)** — master plan: rubric map, actual repo state, **critical items to close
  tonight**, full ~13-slide outline, and the **Part 3 (pipeline → end product) deep dive**.
- **[hero-shows.md](hero-shows.md)** — curated demo subjects + coverage reality (only 147
  full-coverage events; 6 in Bay Area). Recommended: **Everclear @ The Independent**.
- **[queries/hero_shows.sql](queries/hero_shows.sql)** — reusable, deterministic curation query.
  Re-run the morning of the demo to refresh.
- **assets/** — drop diagrams, screenshots, the backup screen recording here.

## Fastest path to a safe demo
1. Read **PLAN.md → Critical Items** (the 8 things that decide the 25 Part-3 points).
2. Freeze a hero show from **hero-shows.md**.
3. Make sure the live app (E2 + F1/F3) reads real gold, or stand up the Looker Studio fallback.
4. Record a backup capture of the working app into `assets/`.

## Re-run the curation query
```bash
bq --project_id=data-architecture-498123 query --use_legacy_sql=false \
   --max_rows=50 < final_presentation/queries/hero_shows.sql
```
