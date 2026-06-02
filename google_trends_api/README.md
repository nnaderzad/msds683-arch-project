# Google Trends POC for Milestone 1

Part of the **music-demand** project (`conda activate music-demand`).

## Purpose

For Milestone 1 we need to prove that Google Trends can supply the **local
search-interest signal** in our live-music demand pipeline — the per-metro
"buzz" leading up to a show, which we join to Ticketmaster events and Spotify
popularity.

This POC answers:

- Can we pull search interest for a roster of artists, per metro, over time?
- Which metros can we resolve, and what are their Google Trends geo codes?
- How noisy is the data, and what granularity gives a usable signal?
- What are the limitations we should flag before committing to this source?

## There is no official Google Trends API

Google does not publish a Trends API. We use [`pytrends`](https://github.com/GeneralMills/pytrends),
which drives the same private endpoint the Trends website uses. Consequences:

- It is **rate-limited** and will occasionally return **HTTP 429**. We pause
  between queries (default 10s) and back off on 429s.
- The endpoint changes without notice, so the library can break; pin versions
  (see `requirements.txt`). Verified working on `pytrends==4.9.2` +
  `pandas==3.0.3`, Python 3.11.
- Transient DNS/network drops to `trends.google.com` look like failures but are
  not rate-limiting — the client retries those too.

## The relative-scale quirk (important for modeling)

Trends values are integers **0–100, normalized within each
(query, geo, timeframe) pull** against that series' own peak. So:

- Values **are** comparable across **time** for one artist in one metro.
- Values are **not** comparable across artists, across metros, or across
  different time windows.

We therefore fetch **one artist + one metro at a time** (never a 5-keyword
comparison batch) so each series keeps its own clean 0–100 scale. Downstream,
treat each `(artist, metro)` series as its own index.

## Geo codes: metros are Nielsen DMAs

pytrends accepts metro geo codes in the form **`US-<STATE>-<DMA>`**
(e.g. `US-CA-807`). Bare `US-<DMA>` returns HTTP 400. We confirmed the codes
against `interest_by_region(resolution="DMA", inc_geo_code=True)`.

We track **10 large US live-music metros** (`config.US_METROS`). These are
weighted toward major touring/EDM markets rather than strict population rank —
Las Vegas, Austin, and Denver matter more for live music than their population
suggests. We deliberately avoid small DMAs, whose low search volume produces
noisy 0/100 spikes.

| Metro | geo code | DMA |
|---|---|---:|
| San Francisco-Oakland-San Jose (anchor) | `US-CA-807` | 807 |
| Los Angeles | `US-CA-803` | 803 |
| New York | `US-NY-501` | 501 |
| Chicago | `US-IL-602` | 602 |
| Las Vegas | `US-NV-839` | 839 |
| Miami-Ft. Lauderdale | `US-FL-528` | 528 |
| Denver | `US-CO-751` | 751 |
| Seattle-Tacoma | `US-WA-819` | 819 |
| Austin | `US-TX-635` | 635 |
| Atlanta | `US-GA-524` | 524 |

The `bay-area` preset narrows to just DMA 807 to line up with the Ticketmaster
POC scope.

## Artist roster

`config.ARTISTS` holds ~32 acts and festivals: a core of touring EDM artists
(Skrillex, Calvin Harris, Deadmau5, Fisher, Charlotte de Witte, Zedd, Illenium,
Kaytranada, ODESZA, John Summit, Fred again.., Sammy Virji, Nimino, Swedish
House Mafia, Four Tet, Ben Böhmer, Laszewo, RÜFÜS DU SOL, Disco Lines, Parcels,
The xx, Empire of the Sun) plus pop/hip-hop/R&B acts with big upcoming Bay Area
shows (Charli XCX, Tinashe, Ariana Grande, Ed Sheeran, Shakira, A$AP Rocky,
Usher, Chris Brown) and two festivals (Portola Festival, Outside Lands).

Each entry has a `query` distinct from its display `name` where the name
collides with common words — e.g. `Fisher` → `"Fisher DJ"`, `Parcels` →
`"Parcels band"`. See *Known limitations*.

## Repo layout

```
google_trends_api/
├── README.md            # this file
├── requirements.txt     # pinned pytrends + pandas (tested combo)
├── config.py            # METROS (geo presets) + ARTISTS roster
├── trends_client.py     # pytrends wrapper: retry/backoff + weekly resample
├── fetch_trends.py      # CLI: fetch (artist x metro) -> tidy CSV
└── sample_data/         # small committed sample pulls (see below)
```

Full local pulls are written to `data/` at the repo root, which is gitignored
(data lives in GCS per the project architecture). Only the small files in
`sample_data/` are committed, so reviewers can see real output without running
anything.

## Install

```bash
conda activate music-demand          # or: conda create -n music-demand python=3.11
pip install -r google_trends_api/requirements.txt
```

## How to run

```bash
# See the metros we track and their geo codes:
python google_trends_api/fetch_trends.py --list-metros

# Default: every roster artist across all 10 US metros, weekly, ~90 days.
python google_trends_api/fetch_trends.py --output data/google_trends/interest.csv

# Bay Area only, EDM only, first 5 artists — quick smoke test:
python google_trends_api/fetch_trends.py --geo bay-area --category edm --limit 5 \
    --output data/google_trends/bay_edm.csv

# Daily granularity, shorter window:
python google_trends_api/fetch_trends.py --granularity daily --timeframe "today 1-m" \
    --output data/google_trends/daily.csv
```

### CLI options

| Flag | Default | Meaning |
|---|---|---|
| `--geo` | `us-metros` | Metro preset: `us-metros` (10) or `bay-area` (1). |
| `--category` | (all) | Restrict to one category: `edm`, `pop`, `hiphop`, `rnb`, `festival`. |
| `--limit` | (none) | Cap number of artists — handy for smoke tests. |
| `--timeframe` | `today 3-m` | Google Trends timeframe string (~90 days). |
| `--granularity` | `weekly` | `weekly` resamples daily → steadier weekly means; or `daily`. |
| `--sleep` | `10` | Seconds between queries to avoid HTTP 429. |
| `--output` | (none) | CSV path; omit for a dry run (fetch + summary, no write). |
| `--list-metros` | — | Print tracked metros and exit. |

Run time scales with `artists × metros × (sleep + request time)`. The full
default run (32 × 10) takes roughly 30–60 minutes because of the polite 10s
pause; use `--limit`/`--category`/`--geo bay-area` for fast iterations.

## Output schema (tidy / long format)

One row per `(artist, metro, date)`:

| Column | Example | Notes |
|---|---|---|
| `extract_ts_utc` | `2026-06-02T04:11:00+00:00` | Run timestamp; repeated runs become snapshots. |
| `artist` | `Fred again..` | Display name. |
| `query` | `Fred again` | Exact term sent to Trends. |
| `category` | `edm` | edm / pop / hiphop / rnb / festival. |
| `geo_name` | `San Francisco-Oakland-San Jose` | Metro label. |
| `geo_code` | `US-CA-807` | Google Trends geo code. |
| `dma` | `807` | Nielsen DMA number (for joins). |
| `date` | `2026-05-25` | Week-ending (weekly) or day (daily). |
| `granularity` | `weekly` | `weekly` or `daily`. |
| `interest` | `43` | 0–100 within this `(artist, geo)` series. |
| `is_partial` | `True` | Google still collecting this period's data. |

Long format joins cleanly: link to Ticketmaster on `(artist, metro, date)` and
to Spotify on `artist`.

## Verified POC results

Tested 2026-06 against live Google Trends:

- **pytrends works on pandas 3.0** — no breakage despite pandas' removal of
  older APIs.
- **Metro series carry real signal.** Daily metro data is sparse (many zero
  days), but spikes are real and weekly means are usable. Example, `today 3-m`,
  daily, "Fred again":

  | Metro | mean | max | non-zero days |
  |---|---:|---:|---:|
  | San Francisco (807) | 4.7 | 100 | 15/93 |
  | Los Angeles (803) | 10.2 | 100 | 24/93 |
  | New York (501) | 14.3 | 100 | 28/93 |

- **DMA breakdown confirms geo codes**: `interest_by_region(resolution="DMA")`
  returns geoCode 807 = San Francisco-Oakland-San Jose, 803 = LA, 501 = NYC.

Sample CSVs from a small live run are committed in `sample_data/` — see that
folder's contents for the exact columns and values.

## Known limitations

- **Ambiguous names.** Short or common-word names pick up unrelated searches.
  We mitigate with `query` overrides (`Fisher` → `Fisher DJ`), but some noise
  remains; topic-level entity queries are a future improvement.
- **Sparse metro data.** Niche acts in a single metro can be mostly zeros over
  90 days. Weekly granularity and/or a longer timeframe help.
- **Relative scale.** Values are not comparable across artists/metros (see
  above). Don't sum or average across series naively.
- **Rate limits / availability.** Long runs risk 429s and the unofficial
  endpoint can change; we pin versions and back off, but this source is best
  treated as periodically refreshed, not real-time.

## Suggested Milestone 1 summary

Google Trends (via pytrends) is a viable **local search-interest** source for
the music-demand pipeline. We resolve 10 large US metros by Nielsen DMA geo code
(anchored on the Bay Area, DMA 807) and pull per-artist, per-metro
interest-over-time as tidy long-format CSVs that join to Ticketmaster events and
Spotify popularity. The data carries real per-metro signal but is relative
(0–100 within each series), sparse at daily/metro resolution, and rate-limited —
so we fetch one artist+metro at a time, pause between queries, resample to
weekly, and treat each series as its own index.
