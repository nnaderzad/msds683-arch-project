# SeatGeek API — proof-of-concept extractor

A second event + price source alongside Ticketmaster. **Why we're adding it:**
Ticketmaster's Discovery API exposes a *face-value* price for only **~23% of
events** (profiled in `analysis/profile_schema.py`). SeatGeek's `/events` `stats`
carry **secondary-market** signals — `lowest_price`, `average_price`, and
`listing_count` (resale inventory = a demand proxy) — which fit our *resale*-demand
thesis better and provide a fallback if Ticketmaster ever breaks.

This directory currently holds a **local POC only** (`seatgeek_poc.py`). A
scheduled collector + Terraform (mirroring the Ticketmaster pipeline) is deferred
until we confirm API access and see the data.

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

- **Demand signals:** `listing_count` / `visible_listing_count` (live resale
  inventory), `lowest_price` / `average_price` / `median_price` / `highest_price`,
  and SeatGeek's own `score` / `popularity`.
- **Joins:** flattened columns mirror `tm_events` (`venue_*`, `price_*`, performers,
  date) so a future `sg_events` silver table joins to Ticketmaster, Trends, and
  YouTube on **artist + venue-DMA + date**.
- **Bonus — venue capacity:** SeatGeek venues sometimes include `capacity`, which is
  otherwise a hand-gathered dimension for us. The richness summary measures how
  often it's filled.
- **No history.** `stats` is a **current snapshot** — there is no price time-series
  in the API. Like Ticketmaster prices and YouTube stats, history only accrues by
  **snapshotting daily** (this script is forward-only). That's the next step once
  access is confirmed.
