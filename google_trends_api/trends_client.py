"""Thin pytrends wrapper for the music-demand project.

Google Trends has no official API; pytrends drives the same private endpoint the
Trends website uses. That means it is rate-limited and will occasionally answer
with HTTP 429. This module centralizes:

- request construction (one keyword + one geo at a time, so the 0-100 scale is
  per-(artist, geo) and never silently rescaled against other artists),
- retry/backoff on 429s,
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
    """Fetch Google Trends interest-over-time series, one (artist, geo) at a time."""

    def __init__(
        self,
        *,
        hl: str = "en-US",
        tz: int = PACIFIC_TZ_OFFSET,
        request_sleep: float = 10.0,
        max_retries: int = 4,
        backoff_seconds: float = 30.0,
    ) -> None:
        # request_sleep is the polite pause between successful queries (the plan
        # calls for ~10s to avoid tripping the 429 rate limit). backoff_seconds
        # is the *additional* escalating wait applied only after a 429.
        self.request_sleep = request_sleep
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        # NOTE: do NOT pass retries=/backoff_factor= here. pytrends 4.9.2 builds a
        # urllib3 Retry with the removed `method_whitelist` kwarg, which raises on
        # urllib3 2.x. We do our own retry/backoff in interest_over_time() instead.
        self._pytrends = TrendReq(hl=hl, tz=tz)

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

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._pytrends.build_payload([query], timeframe=timeframe, geo=geo)
                return self._pytrends.interest_over_time()
            except (TooManyRequestsError, ResponseError) as exc:
                last_error = exc
                wait = self.backoff_seconds * attempt
                logger.warning(
                    "Rate-limited on '%s' @ %s (attempt %d/%d); backing off %.0fs",
                    query, geo, attempt, self.max_retries, wait,
                )
                time.sleep(wait)
            except Exception as exc:  # network blips etc. — retry a few times too
                last_error = exc
                wait = self.backoff_seconds * attempt
                logger.warning(
                    "Error on '%s' @ %s (attempt %d/%d): %s; retrying in %.0fs",
                    query, geo, attempt, self.max_retries, exc, wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"Failed to fetch '{query}' @ {geo} after {self.max_retries} attempts"
        ) from last_error

    def sleep_between_requests(self) -> None:
        """Polite pause between successful queries to avoid HTTP 429."""

        if self.request_sleep > 0:
            time.sleep(self.request_sleep)


def to_weekly(daily: pd.Series) -> pd.Series:
    """Resample a daily 0-100 interest series to weekly means.

    Metro-level daily data is sparse (many zero days); weekly means give a
    steadier signal. We re-round to ints to keep the familiar 0-100 feel.
    """

    weekly = daily.resample("W").mean()
    return weekly.round().astype("Int64")
