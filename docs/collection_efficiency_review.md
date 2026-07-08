# Collection-efficiency review & redesign (2026-07-04)

Decision record for the July 2026 "collect the most data, most efficiently" review.
Sizing numbers come from live BigQuery (reproduce with `eda/collection_sizing.py`);
external facts were web-verified 2026-07-04 (links inline).

## Findings

### Ticketmaster

1. **Pricing coverage is a source ceiling, not a budget problem.** 22.7% of
   observations / ~23% of events carry `priceRanges`. Of 8,870 ever-priced events,
   **8,868 were priced from their first observation** — events either expose price
   from day one or never do. Community + docs picture: `priceRanges` reflects
   TM-host-fulfilled *primary* inventory only; TicketWeb/Universe/venue-system
   shows (i.e. most club EDM) never populate it; resale never appears. The
   Discovery Feed's own price fields are documented "no longer in use; always null".
1a. **Bronze-corpus anatomy (2026-07-04, `eda/tm_bronze_price_eda.py`, 41,489
   distinct events across 4 sampled sweep days):** 23.9% of raw events carry a
   `priceRanges` array; **every one is `type: "standard"` (primary face value) —
   resale ranges occur 0.0% of the time, zero occurrences in the corpus.** When
   present, min/max are always filled (no partial ranges). Onsale timing:
   pre-onsale events are *more* likely priced (43.3%) than post-onsale (23.7%) —
   big tours load prices before onsale — so a "watch unpriced events around
   onsale" heuristic adds nothing; unpriced events are already re-observed every
   sweep at zero marginal cost. Attractions present on ~83–84% of raw events.

1b. **Live probes (2026-07-04, `eda/tm_price_probe.py`):** the single-event
   detail endpoint returned `priceRanges` for **0 of 50** search-unpriced events
   (5/5 priced controls did) — no hidden recovery path; the Commerce offers
   endpoint answers **401** to a standard key (dead/gated); **0 of 52**
   production date-slices approach the 1,000-event deep-paging cap (worst 591) —
   the sweep is not losing events to truncation.

2. **6 sweeps/day ≈ 5 wasted.** `tm_observations` is `(event_id, snapshot_date)`
   daily grain, so intra-day sweeps mostly MERGE into the same row (~3,900 of
   4,680 calls/day buy provenance only: `n_captures`, `price_disagreed`).
3. **Official upgrade path exists**: the [Inventory Status API](https://developer.ticketmaster.com/products-and-docs/apis/inventory-status/)
   returns primary **and resale** price ranges, batched ~350 event IDs/call,
   "authorized clients only" via devportalinquiry@ticketmaster.com — the same
   address that handles Discovery quota increases (granted case-by-case; the
   [FAQ](https://developer.ticketmaster.com/support/faq/) steers bulk users to the
   Discovery Feed, which has no call limits but no prices).
4. **Website scraping (offeradapter/ismds) is not viable**: anti-bot hardening
   (~Aug 2025) breaks plain HTTP clients ([Scrapfly write-up](https://scrapfly.io/blog/posts/how-to-scrape-ticketmaster));
   wrong risk profile for this project anyway.
5. **Multiple API keys**: enforced per key, not per IP; no explicit ToS ban but the
   license's anti-circumvention discretion makes it a gray zone. Not needed while
   quota isn't the binding constraint.

### Google Trends

5b. **The daily job was deadline-capped, not throttle-capped.** Its 3300s
   `RUN_DEADLINE_SECONDS` (under a 3600s Cloud Run timeout) allows ~165 calls at
   the 20s pace — but the queue was 500 units and the budget 800. Bronze confirms:
   28–145 landed files/day. The single biggest Trends fix is simply a longer run
   window (6h timeout / 5h40m deadline), no extra IPs needed.

6. **The remaining bottleneck was targeting, not throughput.** One interest-over-time call
   returns the full ~269-day daily series for an (artist, DMA) pair, so a pair
   needs one call every few days, not one per day. The tier-1 target — upcoming
   headliners in DMA 807 + EDM headliners nationwide — is **1,165 pairs / 982
   artists** (vs 22,443 total pairs, which is why the June backfill took weeks).
   A ~4-day refresh ≈ 300 calls/day fits the existing 800/day single stream.
7. **A GCP VM fleet would not scale linearly.** Community consensus: datacenter
   (GCP/AWS) IP ranges are the worst-throttled class for Trends, flagged at ASN
   level ([cloro.dev](https://cloro.dev/blog/scraping-google-trends/)); rotating
   *residential* IPs scales roughly linearly, datacenter does not. Our single
   Cloud Run stream at 20s spacing already outruns community per-IP guidance.
8. **pytrends is dead** — [repo archived 2025-04-17](https://github.com/GeneralMills/pytrends);
   pinned 4.9.2 still works. Successor if it breaks: [pytrends-modern](https://github.com/yiromo/pytrends-modern)
   (active May 2026; backoff, proxy support) or a paid API.
9. **Official Google Trends API is still invite-only alpha** ([docs](https://developers.google.com/search/apis/trends)):
   ~10k points/day (≈ dozens of term-series/day), **no DMA geo** (country/state
   only), 5-year consistently-scaled daily data. Can't replace the pipeline;
   worth a free application for state-level supplement.
10. **Paid widget APIs are cheap but DMA-on-timeseries is unverified.**
    [DataForSEO](https://dataforseo.com/pricing/keywords-data/google-trends)
    (~$2.25/1k tasks), [SearchAPI.io](https://www.searchapi.io/pricing) (~$4/1k),
    [SerpApi](https://serpapi.com/pricing) (~$15/1k) all replay the same widget
    endpoints (same 0–100 normalization, same 269-day daily window), but none
    documents `geo=US-CA-807` on the TIMESERIES endpoint — **smoke-test on
    SerpApi's free tier (250/mo) before any spend**. If DMA works, a full 9-month
    tier-1 backfill is a ~$6–75 one-off instead of weeks of pytrends grinding.

### Alternative EDM / Bay Area sources (ranked)

11. **19hz.info** — *the* Bay Area electronic-music listing; semi-structured HTML
    with a **price column** (face-value ranges), full multi-artist lineups, genre
    tags, ticket links (Eventbrite/Tixr/Dice/RA/TM), plus a
    [past-events CSV](https://19hz.info/pastEvents_BayArea.php). Covers club and
    warehouse shows TM never lists. Low ToS risk, one polite fetch/day. **Build.**
12. **Ticket-page JSON-LD** (schema.org Event `offers`: price, currency,
    availability/sold-out) — probed live 2026-07-04 against 19hz-discovered URLs:
    **eventbrite.com works** (AggregateOffer low/high) and **shotgun.live works**
    (per-tier Offers incl. SoldOut); **tixr.com and ra.co 403** plain HTTP
    (bot walls); **dice.fm** 19hz links are partner redirects landing on the
    homepage (resolvable later); posh.vip/instagram carry no JSON-LD. **Built**
    (`nineteenhz_api/poll_ticket_pages.py`, allowlist eventbrite+shotgun).
13. **SeatGeek Platform API** — only official free API with resale stats
    (`lowest/median/average_price`, listing counts) but registrations sit in an
    approval queue, often unanswered ([issue](https://github.com/seatgeek/api-support/issues/169)).
    **Apply, don't depend.**
14. **EDMTrain** — free EDM-curated event/lineup API ([docs](https://edmtrain.com/api-documentation));
    **no prices**, and [API terms](https://edmtrain.com/api-terms-of-use) cap
    caching at 24h and forbid storing historical data → join-time lineup
    enrichment only, never a bronze source. **Apply.**
15. **JamBase** — [free non-commercial tier](https://data.jambase.com/pricing)
    (1,000 calls/mo): lineups with billing order + Ticketmaster/Spotify ID
    mappings. Pricing history exists only at $2,500/mo — skip that. **Apply.**
16. **Resident Advisor** — public GraphQL endpoint but scraping is explicitly
    banned by [ToS](https://ra.co/terms). **Rejected.**
17. **Bandsintown / Songkick** (single-artist keys / unanswered approvals),
    **Eventbrite** (public search dead since 2020; venue-scoped listing possible
    later), **StubHub official** (partner-only), **TicketsData** ($499/mo). Rejected
    for now.

### Cross-cutting

17b. **Collection times were ad hoc and misaligned** (TM 06:00/18:00, Trends
    09:00, gold 09:00, YouTube 09:30 — all PT): gold-refresh started the moment
    Trends started, so gold always served *yesterday's* demand signals, and the
    18:00 PT TM sweep landed in the *next* UTC day (`snapshot_date` is UTC), so
    the evening capture joined a different day's Trends/YouTube rows. Physical
    constraints that shape the fix: PT 01:00–15:59 maps into one UTC day
    year-round (15:00 PT = latest same-UTC-day slot); the Trends crawl is
    single-stream ~3–4 h (worst 5h40m) and its content doesn't depend on fetch
    hour (daily series, current partial day unstable — freshest reliable point
    is always yesterday); YouTube takes ~30 min.

18. **Headliner gap caps everything downstream**: only 42.9% of priced events
    resolve a headliner (missing `attraction_names` at source). 498 safe
    title-match recoveries identified (`eda/diagnose_headliner_gap.py`); 19hz +
    EDMTrain lineups join on (venue, date) to recover club shows.

## Decisions

| # | Decision | Rationale (findings) |
|---|---|---|
| D1 | Redeploy TM function; backfill `tm_observations` Jun 19→Jul 1 from bronze | deploy-gap incident (see REPO_STATE) |
| D2 | Cut TM sweeps 6→2/day; keep single key; email TM for Inventory Status access + quota bump | 1, 2, 3, 5 |
| D3 | Rebuild `gtrends-daily`: 6h run window (was 55min — the real cap, see 5b) + tier-1 priority pairs (~1,165, ~4-day rotation) + snapshot thinning, on the existing single stream | 5b, 6 |
| D4 | **No VM fleet** for Trends (was considered); revisit only with residential egress. Per Tomas 2026-07-04: different GCP *regions* don't help either — datacenter ASN flagging is class-wide, not geographic | 7 |
| D5 | **Paid APIs ruled out** (Tomas, 2026-07-04). SerpApi *free tier* DMA smoke test only if a free key appears; apply for the official alpha (free) | 9, 10 |
| D6 | New sources: 19hz collector **(built — 507 events, 75.9% priced)** → ticket-page JSON-LD poller **(built — eventbrite+shotgun)**; EDMTrain dropped; SeatGeek application already filed by Tomas (no answer); JamBase optional (lineup/ID enrichment) if headliner gap persists; RA = permission email first (`docs/ra_access_request.md`), no unpermissioned GraphQL | 11–17 |
| D7 | Promote the 498 safe headliner recoveries into the dimension build **(done — build_dimensions.py title-match recovery)** | 18 |
| D8 | **PT-anchored daily cadence, serve-by 19:00 PT** (2026-07-08): PT = project reference tz (labels everywhere); TM 05:00+15:00, Trends 11:00, YouTube 15:00, gold-refresh 16:30 — every capture in ONE UTC day so `snapshot_date` joins stay same-cycle; gold gains a `fact_trends_daily` incremental load (was frozen — only ever loaded manually); TM completeness gate = stderr summary → ERROR log → existing email alert (already shipped + tested). Full "SF-day" partition migration, DAYS_AHEAD 365, and a Bay-Area-only ~18:00 PT mini-sweep deferred to backlog (team-plan.md). Details: REPO_STATE "Clock & cadence" | 17b, 2 |
