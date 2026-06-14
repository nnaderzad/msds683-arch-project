# YouTube POC

Fetches **streaming/attention momentum** signals for a hardcoded list of
touring electronic artists, using the **YouTube Data API v3**. This replaces the
Spotify signal, whose `popularity` / `followers` / `genres` fields are now
**deprecated** and return `null` under the Client Credentials flow.

For each artist we resolve their channel and pull `statistics`:

| Field | Meaning | Notes |
|-------|---------|-------|
| `subscriberCount` | global popularity proxy | **rounded** by YouTube (~3 sig figs) |
| `viewCount` | cumulative all-time channel views | diff over time = momentum |
| `videoCount` | number of public videos | |

Auth is a **simple API key** for public data — no OAuth / user login required.

## Setup

1. Google Cloud Console → enable **YouTube Data API v3** → **Credentials →
   Create credentials → API key**.
2. Paste the key into `.env` (git-ignored, not pushed):

   ```
   YOUTUBE_API_KEY=AIza...
   ```

3. Install the one dependency:

   ```bash
   pip install -r requirements.txt   # or: conda activate music-demand
   ```

## Run

```bash
python3 youtube_poc.py
```

Prints a per-artist summary plus the full structured JSON payload. Edit the
`ARTISTS` list in `youtube_poc.py` to change which artists are fetched.

## Quota notes

- `channels.list` (stats) = **1 unit/call**.
- `search.list` (name → channel) = **100 units/call** — the expensive part.
  Default quota is 10,000 units/day, so resolve channel IDs once and cache them
  for a production run rather than searching every time.

## Production collector (`collect_youtube.py`)

Turns the POC into the deployed pipeline:

- **Roster-driven:** pulls the artist list from the Ticketmaster silver table
  (`tm_events`) — the same acts the Trends pipeline tracks — instead of a hardcoded
  list, so YouTube / Trends / Ticketmaster all join cleanly on `artist`.
- **Watchlist:** also forward-collects a curated set of popular artists
  (`watchlist.csv`) that aren't in our event data *yet* — YouTube has no history, so
  this avoids permanent gaps if those acts get added later. The collector unions the
  TM roster with the watchlist, de-dups, and caps the total (`YOUTUBE_MAX_ARTISTS`)
  to keep a run under the 10k/day quota.
- **Quota-aware:** resolves each artist's channel IDs **once** and caches them in
  GCS (`gs://…-processed/youtube/channel_cache.json`), capping search spend per run
  (`RESOLVE_MAX_UNITS`, default 8000). Every run then cheaply refreshes stats and
  lands a daily snapshot to `gs://…-raw/youtube/dt=<date>/`. Stops gracefully at the
  daily quota.
- **Signals kept separate:** `official_subscribers` (brand/popularity) and
  `topic_total_views` (audio/streaming momentum), per the official-vs-"- Topic"
  rationale above.

```bash
python youtube_api/collect_youtube.py --top-n 50 --resolve-max-units 4000   # needs YOUTUBE_API_KEY
```

**Deployed** as the `youtube-daily` Cloud Run Job + Scheduler (9:30am PT) in
[`../terraform/gtrends/youtube.tf`](../terraform/gtrends/youtube.tf), running as the
`youtube-ingest` service account and reading the key from the `youtube-api-key`
Secret Manager secret. It shares the ingestion image
([`../google_trends_api/Dockerfile`](../google_trends_api/Dockerfile)) via a command
override.
