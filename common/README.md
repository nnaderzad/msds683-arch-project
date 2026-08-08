# common/ — shared bronze landing + deterministic keys

Two small modules every collector and transform imports, so all sources land
bronze identically and every layer computes the *same* join keys independently
(no build-order dependency).

## `gcs_io.py` — bronze landing

`upload_raw(source, payload, ...)` writes one untouched API capture to the
bronze bucket under the project-wide layout:

```
gs://<project>-raw/<source>/dt=<YYYY-MM-DD>/<source>[_<suffix>]_<UTCstamp>.<ext>
```

`dt=` partitioning keeps every run its own snapshot and lets BigQuery read a
source as a date-partitioned external table. Bucket override: `GCS_RAW_BUCKET`
env var. Auth is ADC (`gcloud auth application-default login`); smoke-test:

```bash
conda activate music-demand
python common/gcs_io.py        # writes a tiny _healthcheck object to the bucket
```

## `keys.py` — deterministic surrogate keys

Stable BLAKE2b hashes of natural keys instead of sequences, so `fact_trends`,
`fact_youtube`, the dims, and gold can each be built independently and still
join — same input, same id, every run, no DB round-trip.

| Function | Key |
|---|---|
| `normalize_name(name)` | whitespace-collapsed, casefolded artist name — THE cross-source natural key |
| `artist_id(name)` | positive int63 hash of the normalized name |
| `venue_id(tm_venue_id)` | positive int63 hash of the Ticketmaster venue id |
| `snapshot_id(*parts)` | hex hash of a fact row's business key — the MERGE key that makes every silver loader idempotent |

**CRITICAL:** `artist_id` is a *name* hash, so Ticketmaster and Google Trends
must emit **identical normalized names** or the artist join silently breaks.
Rosters may send a disambiguated `query` to Trends ("Fisher DJ") while keeping
the TM display `name` ("Fisher") as the key — never swap the two. Trends geo
codes are `US-<ST>-<DMA>`; stripping to the bare DMA number for joins is done
by `bare_dma()` (lives in `pipeline/silver/trends_series_to_silver.py`).

Where these keys land in the schema:
[`../docs/data-model.md`](../docs/data-model.md).
