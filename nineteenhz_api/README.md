# 19hz.info collector — Bay Area club-show prices + lineups

[19hz.info](https://19hz.info/eventlisting_BayArea.php) is the canonical Bay Area
electronic-music listing: club and warehouse shows across Dice, Tixr, Eventbrite,
RA, Posh, TicketWeb — the events where Ticketmaster's `priceRanges` is
structurally empty — **with a face-value price column and full multi-artist
lineups**. First live pull (2026-07-04): 507 upcoming events, **75.9% priced**
(vs ~23% on TM), 100% with lineup and venue, ~8 months of forward window.

Why it exists / how it fits the join model: `docs/collection_efficiency_review.md`
(finding 11, decision D6). Joins to `tm_events` / `dim_venue` on (venue, date);
lineups also feed the headliner-gap repair.

## Run

```bash
python nineteenhz_api/collect_19hz.py --dry-run                      # fetch + richness summary
python nineteenhz_api/collect_19hz.py --output data/nineteenhz/events.csv
python nineteenhz_api/collect_19hz.py --output ... --land-raw        # + untouched HTML to bronze
```

Standard library only (`--land-raw` needs `google-cloud-storage`, like the other
collectors). One request per run; run at most daily — it's a hobbyist site, be
polite. Bronze layout matches the other sources:
`gs://<raw>/nineteenhz/dt=<date>/nineteenhz_bayarea_<stamp>.html`.

## Output columns

Tidy long format, one row per event, stamped with `extract_ts_utc` so repeated
runs become price-history snapshots: `event_date`, `datetime_text`, `title`,
`venue`, `city`, `genres`, `price_text/price_min/price_max/is_free/price_open_ended`,
`age_restriction`, `organizers`, `artists` (pipe-delimited lineup), `n_artists`,
`ticket_url`, `ticket_domain`.

## Parsing quirks handled (see tests/test_collect_19hz.py)

- The site nests genre tags as an **unclosed `<td>`** inside the title cell —
  cells are split manually on `<td>` boundaries.
- The hidden `<div class='shrink'>YYYY/MM/DD</div>` cell is the machine-readable
  start date (multi-day festivals keep their start date).
- Lineups come from title conventions (`"Party: A, B b2b C"`); `b2b`/`B2B` pairs
  are exploded; "and more / TBA / guests" placeholders are dropped.
- Prices: `$68-80`, `$183+` (open-ended flag), `free` → 0.0, missing → NULL.

## Backlog

- One-time ingest of the site's published past-events CSV (
  `https://19hz.info/pastEvents_BayArea.php`) for history.
- Dice/Tixr JSON-LD poller seeded from this collector's `ticket_url`s for
  per-event daily price/sold-out series (decision D6, step 2).
- Cloud Run job + terraform once the POC has proven a few days of daily runs.
