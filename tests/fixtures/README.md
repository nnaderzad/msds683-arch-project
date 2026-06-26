# Cross-source seed slice (task T0)

A tiny, **real**, committed slice of the bronze data covering a handful of artists
that appear in **all three sources** — the offline test substrate the rest of the
build runs against. Phase-1 silver transforms (`A1`/`A2`/`A3`) and the bronze→silver
integration test (`INT-1`) unit-test against this seed so they stay **deterministic
and network-free** (no live BigQuery/GCS in CI).

Nothing here is synthetic: every record is trimmed from what the collectors actually
landed in `gs://data-architecture-498123-raw/`.

## What's in `seed/`

```
seed/
  manifest.json                       provenance + the exact picks (see below)
  ticketmaster/
    tm_events_seed.csv                seed events' tm_events rows × every snapshot day
    dt=<latest>/ticketmaster_seed.json  trimmed RAW bronze event objects (one per seed event)
  youtube/dt=<day>/youtube_*.json     daily payloads, records filtered to the seed artists
  google_trends/dt=<day>/google_trends_{iot_US,ibr_DMA}_<artist>_*.json
                                      seed artists' national series + DMA snapshot, verbatim
```

The layout mirrors real bronze on purpose, so a transform reads the seed exactly the
way it reads production.

## Selection rule (deterministic)

Implemented in `build_seed.py`; documented here so a refresh diffs cleanly:

1. Build the Ticketmaster **price panel** from the daily processed snapshots (reusing
   `eda/tm_price_eda.py`) and keep events with a **complete** price history — present
   on ~all of the window **and** priced on ~every present day (H1's `is_complete`,
   span ≥ 0.9 and price-completeness ≥ 0.9). A row that exists daily but has no price
   is worthless, so completeness — not row count — is the bar.
2. Keep only artists present in **all three** sources: a YouTube channel record **and**
   both Trends units (national `iot_US` + DMA snapshot `ibr_DMA`). The join key across
   every source is the artist **display name** (Trends files are named `slug(name)`,
   YouTube records carry `query` = name, TM `attraction_names` = name).
3. Rank artists by `(has a Bay Area show, #complete events, best completeness, soonest
   show, name)` and take the top `--n-artists`, **guaranteeing ≥1 Bay Area show** if
   one exists in the intersection (the demo lens). One representative complete event
   per artist is the seed.

## The guarantee tests rely on

Every seed artist is present in **all three sources** (`test_seed_fixtures.py` asserts
this). That's what makes a cross-source `bronze→silver→gold` join testable on the seed.

## Honest notes (for Q&A)

- **Trends depth varies per artist.** The backfill is single-stream and rate-limited
  (~20 s/call, daily budget), so some artists have many days of Trends and some only a
  few. The seed reflects that real collection-throughput constraint rather than hiding
  it; every seed artist still has ≥1 national + ≥1 snapshot.
- **Price is face value only.** Ticketmaster exposes one `standard` price range; there
  is **no resale/secondary** price in the feed (SeatGeek's is gated). The seed carries
  what exists.
- **The seed is a snapshot.** Re-running the builder later will re-pick artists/events
  as more data lands and more shows become complete — that's the point.

## Refreshing the seed

```bash
# repo root, with `bq` + `gsutil` authed for the project (ADC)
python tests/fixtures/build_seed.py --n-artists 5
git add tests/fixtures/seed && git diff --cached --stat   # review what moved, then commit
pytest tests/test_seed_fixtures.py -q                       # still green after a refresh
```

The builder is ADC-gated and is **not** run in CI — CI only consumes the committed
fixtures, fully offline.
