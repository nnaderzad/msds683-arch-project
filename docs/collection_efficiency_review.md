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

6. **The bottleneck was targeting, not throughput.** One interest-over-time call
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
12. **Dice.fm / Tixr event pages** embed schema.org JSON-LD `offers` (all-in
    price, availability/sold-out). No public API. Poll JSON-LD daily for events
    discovered via 19hz links → clean club-show price series. Moderate-low risk
    (JSON-LD is machine-readable by design; tracked events only). **Build after 19hz.**
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

18. **Headliner gap caps everything downstream**: only 42.9% of priced events
    resolve a headliner (missing `attraction_names` at source). 498 safe
    title-match recoveries identified (`eda/diagnose_headliner_gap.py`); 19hz +
    EDMTrain lineups join on (venue, date) to recover club shows.

## Decisions

| # | Decision | Rationale (findings) |
|---|---|---|
| D1 | Redeploy TM function; backfill `tm_observations` Jun 19→Jul 1 from bronze | deploy-gap incident (see REPO_STATE) |
| D2 | Cut TM sweeps 6→2/day; keep single key; email TM for Inventory Status access + quota bump | 1, 2, 3, 5 |
| D3 | Rebuild `gtrends-daily` around tier-1 priority pairs (~1,165, ~4-day rotation) on the existing single stream | 6 |
| D4 | **No VM fleet** for Trends (was considered); revisit only with residential egress | 7 |
| D5 | Smoke-test paid-API DMA support (SerpApi free tier) before any backfill spend; apply for official alpha | 9, 10 |
| D6 | New sources: 19hz collector (prices+lineups) → Dice/Tixr JSON-LD poller; applications to SeatGeek/EDMTrain/JamBase; RA rejected | 11–17 |
| D7 | Promote the 498 safe headliner recoveries into the dimension build | 18 |
