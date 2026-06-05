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
