# Artist external-links enrichment — opportunity assessment & handoff

**Status: proposal (2026-07-08) — no code exists yet.** This doc sizes the
opportunity and hands off to whoever implements it. Read
`docs/REPO_STATE.md` and `docs/collection_efficiency_review.md` first for
project context and standing constraints.

## The finding

The 2026-07 data review's field inventory (`eda/output/data_review.md`,
§ "Field inventory"; regenerate with `python eda/data_review.py`) measured what
the Ticketmaster Discovery API *actually* returns, across 5,062 events in the
newest CA capture. Buried in `_embedded.attractions[].externalLinks.*` — fields
we currently drop on the floor (only `attraction_ids`/`attraction_names`
survive `flatten_event`) — TM hands us **stable third-party artist IDs**:

| link type | fill (of events, CA capture) | what it is |
|---|---|---|
| `musicbrainz[].id` | **59%** | the artist's **MBID** — the music industry's canonical identifier, handed to us as a plain string, **no API call needed** |
| `spotify[].url` | **58%** | Spotify artist page → artist ID parses straight out of the URL |
| `instagram[].url` | 61% | profile URL |
| `facebook` / `homepage` / `twitter` / `itunes` | 51–56% | profile/store URLs |
| `wiki` | 33% | Wikipedia article |
| `lastfm` | 22% | last.fm artist page |
| `tiktok` / `bandcamp` / `soundcloud` / `vevo` | ≤4% | sparse |

All of this is **already in bronze** for every sweep since June — extraction is
pure parsing, zero new collection.

## Why it matters (mapped to our known bottlenecks)

1. **Artist identity is our weakest join.** Everything joins on
   `artist_id` = hash of the normalized display name (`common/keys.py`), which
   is fragile (the `Fisher` → `"Fisher DJ"` disambiguation hack; 19hz/RA
   lineups will collide with it more). MBID + Spotify ID are *stable canonical
   keys* — store them on `dim_artist` and cross-source identity stops
   depending on spelling.
2. **A third demand signal, better than YouTube.** Spotify's artist object
   carries **`popularity` (0–100, recency-weighted) and `followers`** — a
   direct, daily-refreshable artist-demand proxy. We currently have Trends
   (geo-resolved but throttled) and YouTube (no geo, slow-moving). Spotify
   popularity snapshots would be a `fact_spotify` twin of `fact_youtube`.
3. **Genre quality.** TM's `genre` is coarse and often wrong (the review's
   sample event: a worship-music tour tagged `Religious/Gospel`; club EDM is
   frequently `Other`). Spotify returns curated per-artist genre lists —
   directly usable for the genre features/filter the 19hz/RA work wants.
4. **Roster targeting.** Trends tier-1 selection ranks by priced-event
   headliners; Spotify popularity is a cleaner tiebreaker than touring
   breadth for spending the 800-call/day Trends budget.

## Feasibility & benefit per link type

| source | benefit | cost/difficulty | verdict |
|---|---|---|---|
| **MusicBrainz ID** | canonical identity, aliases, wikidata/discogs bridges | **zero API calls for the ID itself** (it's in bronze); optional MB API lookups are free, no key, 1 req/s etiquette + custom UA | **do first** |
| **Spotify Web API** | popularity + followers time series; genres; canonical name | free dev app (client-credentials flow), `GET /v2/artists?ids=` batches **50 artists/call** → our ~2k-artist universe ≈ 40 calls/day; secret via Secret Manager like the TM key. ToS check needed: metadata caching/retention clauses | **core build** |
| last.fm | listeners/playcounts (another popularity axis) | free API key; 22% link fill but name-search fills gaps | optional later |
| Instagram | follower counts (social demand) | no free API (Graph API = per-account business auth); scraping is ToS-hostile | **park** |
| wiki / itunes / homepage / facebook / twitter / tiktok…| marginal | — | park |

## Proposed shape (for the implementing agent)

- **Phase 0 — extract what we already own (pure parsing, no API).**
  Deterministic loader `pipeline/silver/artist_links_to_silver.py`: walk recent
  TM bronze sweeps' `attractions[]`, emit one row per attraction —
  `(tm_attraction_id, artist_name, artist_id_hash, mbid, spotify_id,
  instagram_url, lastfm_url, …)` — staging+MERGE keyed on `tm_attraction_id`,
  mirroring the existing loader pattern. Then measure: coverage vs
  `dim_artist`, vs the Trends tier-1 roster, on the **nationwide** corpus (the
  59%/58% fills above are CA-only — reproduce on all states first).
- **Phase 1 — Spotify snapshot collector.** Cloud Run job following
  `youtube_api/` (same Terraform root/pattern, same bronze layout:
  `spotify/dt=<UTC-day>/`): batch-resolve the roster's spotify_ids, land raw,
  load `fact_spotify (artist_id, snapshot_date, popularity, followers)` +
  genre list into a small `dim_artist_genre`. Schedule inside the D8 one-UTC-day
  window (e.g. 14:00 PT, before gold at 16:30 PT).
- **Phase 2 — consume.** Popularity into `fact_event_demand`/model features;
  genres into the API/UI genre filter; MBID/Spotify ID onto `dim_artist` as
  the cross-source identity spine (19hz/RA lineups join through it).

## Handoff checklist (next agent, read before coding)

- Evidence to reproduce first: `python eda/data_review.py` (field inventory
  section) — then extend it or `eda/collection_sizing.py` with the nationwide
  link-coverage measurement; keep every number in a committed script.
- Standing constraints: **no paid APIs**; deterministic re-runnable collectors
  (no LLM at runtime); secrets in Secret Manager, never in code; offline unit
  tests for pure transforms; small focused commits; PR for Tomas to review —
  he merges; prod deploys need his explicit go-ahead; update
  `docs/REPO_STATE.md` with every behavior change; PT labels on times.
- Verify before depending: Spotify developer ToS on caching/retention of
  metadata (flag anything restrictive to Tomas before building); MusicBrainz
  rate etiquette (1 req/s, descriptive User-Agent) if MB API calls are added.
- Needs from Tomas: a Spotify developer app (client ID/secret) → Secret
  Manager; a decision on whether Phase 1 waits for the 19hz/RA silver work or
  runs parallel.

## Open questions

1. Nationwide link fill — is 58–59% stable across states, and what is it on
   the **tier-1 roster** specifically (the artists we spend budget on)?
2. Do Spotify popularity/followers move fast enough day-to-day to be a useful
   *time series*, or is weekly enough? (Measure after ~2 weeks of snapshots.)
3. Does MBID meaningfully improve the 19hz/RA lineup joins vs name-hash alone?
