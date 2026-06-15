# SeatGeek API — proof-of-concept extractor

A second event source alongside Ticketmaster. **Original goal:** Ticketmaster's
Discovery API exposes a *face-value* price for only **~23% of events** (profiled in
`analysis/profile_schema.py`), so we hoped SeatGeek's `/events` `stats`
(`lowest_price`, `average_price`, `listing_count` = resale inventory) would give a
richer *resale*-demand signal.

> ## ⚠️ Finding (verified 2026-06-15 with the `shakshuka` app)
> **SeatGeek's pricing `stats` are GATED — the basic client_id tier returns
> `stats = {}` for every event**, including next weekend's stadium tours (Morgan
> Wallen, Ed Sheeran, Ariana Grande) and major future tours (KAROL G). Confirmed
> with and without `client_secret`, so it's an access tier, not "no inventory."
> Getting prices/`listing_count` would require **SeatGeek partner/affiliate
> approval**.
>
> **What the basic tier DOES give us (and why it's still worth keeping):**
> - **`score` / `popularity`** — SeatGeek's own event-demand model, populated for
>   all events (a signal Ticketmaster doesn't have; no geo though).
> - **Venue `capacity`** — populated for a meaningful share of venues (~37% in a CA
>   sample; e.g. Morgan Wallen 61,500, Ariana 18,000) → helps fill `dim_venue.capacity`.
> - Full event/venue/performer/genre catalog (~34k concerts) — mostly redundant with TM.
>
> **So SeatGeek is repositioned from a *price* source to a *popularity + capacity*
> enrichment source.** The resale-price gap is still open (pursue partner access, or
> Eventbrite/Bandsintown/Resident Advisor — see the team backlog).

This directory holds a **local POC only** (`seatgeek_poc.py`). A scheduled collector
+ Terraform is deferred (and lower-value now that pricing is gated).

## Step 0 — verify you can get API access (do this first)

SeatGeek's Platform API access has become **gated**; self-serve app creation may
no longer be open to everyone. Confirm before investing in a full build:

1. Sign in / create an account, then open **https://seatgeek.com/account/develop**.
2. If you can create an app, copy its **`client_id`** (and `client_secret` if shown).
3. Smoke-test the key (200 + JSON = access works; 401/403 = not approved):
   ```bash
   curl "https://api.seatgeek.com/2/events?client_id=YOUR_CLIENT_ID&per_page=1"
   ```
4. If app creation isn't available, you'll need to **apply for partner/affiliate
   access** (or pivot to the fallback sources noted in the team backlog:
   Eventbrite, Bandsintown, Resident Advisor).

## Step 1 — provide the key

Either export it, or put it in a git-ignored `seatgeek_api/.env` (the `.env`
pattern is already covered by `.gitignore`):

```bash
# seatgeek_api/.env
SEATGEEK_CLIENT_ID=your_client_id
SEATGEEK_CLIENT_SECRET=your_client_secret   # optional
```

## Step 2 — run the POC

```bash
# Standard library only — no pip install needed (certifi used for HTTPS if present).
python seatgeek_api/seatgeek_poc.py --city "San Francisco" --state CA --output sf_seatgeek.csv
python seatgeek_api/seatgeek_poc.py --query "Greek Theatre"
python seatgeek_api/seatgeek_poc.py --state CA --upload-raw   # land raw JSON to bronze (needs ADC)
```

Each run prints a **data-richness summary** whose headline number is **price
coverage vs Ticketmaster's ~23%**, plus how often `listing_count` (resale demand)
and `venue_capacity` are populated.

## What we get (and don't)

- **Available now (basic tier):** `score` / `popularity` (SeatGeek's demand model),
  venue `capacity` (partial), and the full event/venue/performer/genre catalog.
- **Gated (partner tier only):** `listing_count` / `visible_listing_count` and all
  `lowest/average/median/highest_price` fields — these come back empty (`stats={}`).
  The POC still captures these columns so they light up automatically if partner
  access is ever granted.
- **Joins:** flattened columns mirror `tm_events` (`venue_*`, performers, date) so a
  future `sg_events` table joins to Ticketmaster, Trends, and YouTube on
  **artist + venue-DMA + date**.
- **No history** even at the partner tier — `stats` is a current snapshot, so history
  (if unlocked) would only accrue by **snapshotting daily** (this script is forward-only).
