"""OHLCV data fetcher with local and cloud cache support.

Fetches price data from yfinance and caches it:
- Local mode: in-memory dict with TTL
- Cloud mode: Postgres cache stub (DATABASE_URL required)

All returned DataFrames have columns: Open, High, Low, Close, Volume
with a DatetimeIndex sorted oldest-first.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Final

import pandas as pd
import yfinance as yf

from signals_app.config import (
    DEFAULT_PERIOD,
    MAX_RETRY_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    VALID_PERIODS,
    Settings,
    get_settings,
)

logger = logging.getLogger(__name__)

# yfinance period → reasonable interval mapping
PERIOD_TO_INTERVAL: Final[dict[str, str]] = {
    "15m": "2m",
    "1h": "5m",
    "4h": "15m",
    "1d": "1d",
    "5d": "1d",
    "1mo": "1d",
    "3mo": "1d",
    "6mo": "1d",
    "1y": "1d",
    "2y": "1wk",
    "5y": "1wk",
    "10y": "1mo",
    "ytd": "1d",
    "max": "1mo",
}

# In-memory cache: (symbol, period) → (df, expires_at)
_MEM_CACHE: dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}

# Cache TTL by period in seconds
CACHE_TTL_BY_PERIOD: Final[dict[str, int]] = {
    "15m": 120,
    "1h": 300,
    "4h": 300,
    "1d": 300,
    "5d": 3600,
    "1mo": 3600,
    "3mo": 3600,
    "6mo": 14400,
    "1y": 14400,
    "2y": 86400,
    "5y": 86400,
    "10y": 86400,
    "ytd": 3600,
    "max": 86400,
}


@dataclass
class OHLCVResult:
    """Result of a data fetch operation.

    Attributes:
        symbol: Ticker symbol.
        period: Period string.
        df: OHLCV DataFrame sorted oldest-first.
        from_cache: True if the data was served from cache.
        bar_count: Number of bars in the DataFrame.
    """

    symbol: str
    period: str
    df: pd.DataFrame
    from_cache: bool
    bar_count: int


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance DataFrame to standard column names.

    yfinance may return multi-level columns or different column names
    depending on version and mode.

    Args:
        df: Raw yfinance DataFrame.

    Returns:
        Normalized DataFrame with Open, High, Low, Close, Volume columns.

    Raises:
        ValueError: If required columns cannot be found.
    """
    # Flatten multi-index columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    # Map common alternative column names
    renames: dict[str, str] = {}
    col_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "adj close": "Close",
        "volume": "Volume",
    }
    for col in df.columns:
        lower = col.lower()
        if lower in col_map and col_map[lower] not in df.columns:
            renames[col] = col_map[lower]

    if renames:
        df = df.rename(columns=renames)

    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"yfinance DataFrame missing columns: {missing}")

    return df[["Open", "High", "Low", "Close", "Volume"]].copy()


def _mem_cache_get(symbol: str, period: str) -> pd.DataFrame | None:
    """Retrieve from in-memory cache if not expired.

    Args:
        symbol: Ticker symbol.
        period: Period string.

    Returns:
        Cached DataFrame or None.
    """
    key = (symbol.upper(), period)
    entry = _MEM_CACHE.get(key)
    if entry is None:
        return None
    df, expires_at = entry
    if time.time() > expires_at:
        del _MEM_CACHE[key]
        return None
    return df


def _mem_cache_set(symbol: str, period: str, df: pd.DataFrame) -> None:
    """Store in in-memory cache with TTL.

    Args:
        symbol: Ticker symbol.
        period: Period string.
        df: DataFrame to cache.
    """
    key = (symbol.upper(), period)
    ttl = CACHE_TTL_BY_PERIOD.get(period, 3600)
    _MEM_CACHE[key] = (df, time.time() + ttl)


def _fetch_from_yfinance(symbol: str, period: str) -> pd.DataFrame:
    """Fetch OHLCV data from yfinance with retry logic.

    Args:
        symbol: Ticker symbol.
        period: yfinance period string.

    Returns:
        Normalized OHLCV DataFrame sorted oldest-first.

    Raises:
        ValueError: If symbol is invalid or data is empty after retries.
    """
    interval = PERIOD_TO_INTERVAL.get(period, "1d")

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            logger.debug(
                "yfinance fetch: %s period=%s interval=%s attempt=%d",
                symbol, period, interval, attempt,
            )
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, auto_adjust=True)

            if df.empty:
                raise ValueError(f"yfinance returned empty data for {symbol} period={period}")

            df = _normalize_df(df)

            # Sort oldest-first
            df.sort_index(inplace=True)

            logger.debug(
                "yfinance fetch: %s — %d bars", symbol, len(df)
            )
            return df

        except ValueError:
            raise
        except Exception as exc:
            logger.warning(
                "yfinance fetch attempt %d failed for %s: %s", attempt, symbol, exc
            )
            if attempt < MAX_RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise ValueError(
        f"Failed to fetch data for {symbol} after {MAX_RETRY_ATTEMPTS} attempts"
    )


class DataFetcher:
    """OHLCV data fetcher with caching.

    Switches between local in-memory cache and a Postgres cache stub based on
    the SIGNALS_ENV environment variable.

    Example:
        fetcher = DataFetcher()
        result = fetcher.fetch("AAPL", "3mo")
        df = result.df  # DataFrame with indicators ready to compute
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize DataFetcher.

        Args:
            settings: Application settings. Loaded from env if None.
        """
        self._settings = settings or get_settings()

    def fetch(self, symbol: str, period: str = DEFAULT_PERIOD) -> OHLCVResult:
        """Fetch OHLCV data for a symbol over a given period.

        Checks the cache first. Falls back to yfinance if not cached.

        Args:
            symbol: Ticker symbol (e.g., "AAPL").
            period: Period string. Must be in VALID_PERIODS.

        Returns:
            OHLCVResult with the DataFrame and metadata.

        Raises:
            ValueError: If period is invalid or data cannot be fetched.
        """
        if period not in VALID_PERIODS:
            raise ValueError(f"Invalid period '{period}'. Valid: {VALID_PERIODS}")

        symbol = symbol.upper().strip()

        # Try local in-memory cache first
        cached_df = _mem_cache_get(symbol, period)
        if cached_df is not None:
            logger.info("data_cache_hit: %s period=%s bars=%d", symbol, period, len(cached_df))
            return OHLCVResult(
                symbol=symbol,
                period=period,
                df=cached_df,
                from_cache=True,
                bar_count=len(cached_df),
            )

        df = _fetch_from_yfinance(symbol, period)
        _mem_cache_set(symbol, period, df)

        logger.info(
            "data_fetched: %s period=%s bars=%d", symbol, period, len(df)
        )
        return OHLCVResult(
            symbol=symbol,
            period=period,
            df=df,
            from_cache=False,
            bar_count=len(df),
        )

    def fetch_multi(
        self,
        symbol: str,
        periods: list[str] | None = None,
    ) -> dict[str, OHLCVResult]:
        """Fetch OHLCV data for multiple periods.

        Args:
            symbol: Ticker symbol.
            periods: List of period strings. Defaults to ["1d", "5d", "1mo", "3mo", "6mo"].

        Returns:
            Dict of period → OHLCVResult for successfully fetched periods.
        """
        if periods is None:
            periods = ["1d", "5d", "1mo", "3mo", "6mo"]

        results: dict[str, OHLCVResult] = {}
        for period in periods:
            try:
                results[period] = self.fetch(symbol, period)
            except Exception as exc:
                logger.warning(
                    "fetch_multi: %s period=%s failed: %s", symbol, period, exc
                )

        return results
