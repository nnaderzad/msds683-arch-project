"""Thin pytrends wrapper for the music-demand project.

Google Trends has no official API; pytrends drives the same private endpoint the
Trends website uses. That means it is rate-limited and will occasionally answer
with HTTP 429. This module centralizes:

- request construction (one keyword + one geo at a time, so the 0-100 scale is
  per-(artist, geo) and never silently rescaled against other artists),
- a deterministic rate cap (one call per ``min_interval`` seconds) plus a small
  retry/backoff on 429s,
- the daily -> weekly resampling we use to tame metro-level noise.

The 0-100 values are relative within each (artist, geo, timeframe) pull, so they
are comparable across *time* for one artist in one metro, but NOT across artists
or across metros. Downstream modeling should treat them as per-series indices.
"""

from __future__ import annotations

import logging
import time

import pandas as pd
from pytrends.request import TrendReq

try:  # pytrends moved/renamed exceptions across versions; stay defensive.
    from pytrends.exceptions import TooManyRequestsError, ResponseError
except ImportError:  # pragma: no cover - older/newer pytrends
    class TooManyRequestsError(Exception):
        ...

    class ResponseError(Exception):
        ...


logger = logging.getLogger(__name__)

# tz=480 is US Pacific (minutes west of UTC), matching the Bay Area anchor.
PACIFIC_TZ_OFFSET = 480


class TrendsClient:
    """Fetch Google Trends interest-over-time series, one (artist, geo) at a time.

    Rate is capped *deterministically*: ``_pace`` enforces at least ``min_interval``
    seconds between successive call starts, so the achieved request rate is exactly
    ``1 call / min_interval`` no matter how long each call takes. Run this as a single
    stream (no parallel shards) and the cap is global. ``calls_made`` is the per-run
    ledger of real API hits (retries included) the job uses to enforce a daily budget.
    """

    def __init__(
        self,
        *,
        hl: str = "en-US",
        tz: int = PACIFIC_TZ_OFFSET,
        min_interval: float = 20.0,
        max_retries: int = 2,
        backoff_seconds: float = 20.0,
    ) -> None:
        # min_interval is the DETERMINISTIC rate cap: minimum seconds between
        # successive call *starts* (enforced in _pace, retries included), so the rate
        # is exactly 1 call / min_interval. One stream at 20s => <=3 calls/min. Pacing
        # is timing only; it never changes the data fetched.
        # backoff_seconds is the per-attempt 429 retry wait (deterministic, linear).
        # max_retries is intentionally low: a rate-limited unit is cheap to retry on a
        # later run (Trends data is backfillable), so we fail fast rather than burn the
        # daily budget grinding on a throttled IP.
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        # Every fetch attempt (incl. retries) increments calls_made — the deterministic
        # per-process call ledger the job reconciles against the daily budget.
        self.calls_made = 0
        self._last_call_ts: float | None = None
        # NOTE: do NOT pass retries=/backoff_factor= here. pytrends 4.9.2 builds a
        # urllib3 Retry with the removed `method_whitelist` kwarg, which raises on
        # urllib3 2.x. We do our own retry/backoff in interest_over_time() instead.
        self._pytrends = TrendReq(hl=hl, tz=tz)

    def _pace(self) -> None:
        """Deterministic rate gate run before every fetch attempt.

        Blocks until ``min_interval`` has elapsed since the previous call start, then
        records this start and counts it. Because it runs on retries too, the achieved
        rate is exactly ``1 call / min_interval`` and ``calls_made`` reflects real API
        hits rather than just successes.
        """

        if self.min_interval > 0 and self._last_call_ts is not None:
            wait = self.min_interval - (time.monotonic() - self._last_call_ts)
            if wait > 0:
                time.sleep(wait)
        self._last_call_ts = time.monotonic()
        self.calls_made += 1

    def _run_with_retry(self, label: str, build_and_fetch):
        """Pace, then run a build_payload+fetch with retry/backoff on 429s.

        ``build_and_fetch`` is a zero-arg callable that (re)builds the pytrends
        payload and returns the desired frame. It is re-invoked from scratch on each
        attempt, so a retry never reuses a half-built payload. Every attempt is rate-
        gated by ``_pace`` first. We do NOT sleep after the final attempt — a throttled
        unit just rolls to a later run.
        """

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._pace()
            try:
                return build_and_fetch()
            except (TooManyRequestsError, ResponseError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    wait = self.backoff_seconds * attempt
                    logger.warning(
                        "Rate-limited on %s (attempt %d/%d); backing off %.0fs",
                        label, attempt, self.max_retries, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Rate-limited on %s (attempt %d/%d); giving up (rolls to a later run)",
                        label, attempt, self.max_retries,
                    )
            except Exception as exc:  # network blips etc. — retry a few times too
                last_error = exc
                if attempt < self.max_retries:
                    wait = self.backoff_seconds * attempt
                    logger.warning(
                        "Error on %s (attempt %d/%d): %s; retrying in %.0fs",
                        label, attempt, self.max_retries, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Error on %s (attempt %d/%d): %s; giving up",
                        label, attempt, self.max_retries, exc,
                    )

        raise RuntimeError(
            f"Failed to fetch {label} after {self.max_retries} attempts"
        ) from last_error

    def interest_over_time(
        self,
        query: str,
        geo: str,
        timeframe: str = "today 3-m",
    ) -> pd.DataFrame:
        """Return the raw pytrends interest-over-time frame for one query+geo.

        Columns: ``[<query>, "isPartial"]`` indexed by date. Empty frame if
        Google has no data for that query/geo (common for niche acts in a metro).
        """

        def _fetch() -> pd.DataFrame:
            self._pytrends.build_payload([query], timeframe=timeframe, geo=geo)
            return self._pytrends.interest_over_time()

        return self._run_with_retry(f"'{query}' @ {geo or 'US'} [{timeframe}]", _fetch)

    def interest_by_region(
        self,
        query: str,
        *,
        geo: str = "US",
        resolution: str = "DMA",
        timeframe: str = "today 12-m",
        inc_geo_code: bool = True,
    ) -> pd.DataFrame:
        """Return one query's interest broken down by region (default: all US DMAs).

        A single call returns every region at ``resolution`` (~210 rows for DMA) —
        the cheapest way to get the geographic distribution of interest. Indexed by
        region name; includes a ``geoCode`` column when ``inc_geo_code`` is set.
        Values are 0–100 relative within this single call.
        """

        def _fetch() -> pd.DataFrame:
            self._pytrends.build_payload([query], timeframe=timeframe, geo=geo)
            return self._pytrends.interest_by_region(
                resolution=resolution, inc_geo_code=inc_geo_code
            )

        return self._run_with_retry(
            f"'{query}' by {resolution} @ {geo} [{timeframe}]", _fetch
        )


def to_weekly(daily: pd.Series) -> pd.Series:
    """Resample a daily 0-100 interest series to weekly means.

    Metro-level daily data is sparse (many zero days); weekly means give a
    steadier signal. We re-round to ints to keep the familiar 0-100 feel.
    """

    weekly = daily.resample("W").mean()
    return weekly.round().astype("Int64")
