"""Regression tests for the indicator-warmup fetch widening in DataFetcher.

Covers the 2026-09-01 universe-scan bug: a "3mo" request (~63 daily bars) was
fetched at exactly that length and fed straight into compute_indicators,
whose 200-period SMAs then silently collapsed to a 63-bar mean
(min_periods=1). DataFetcher.fetch() now transparently widens the underlying
yfinance request for daily-interval periods so the df it returns actually
supports the indicators the caller is about to compute — see
`_WARMUP_PERIOD_OVERRIDE` in signals_app.data.fetcher.

Network is mocked throughout; no real yfinance calls are made.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from signals_app.data.fetcher import _MEM_CACHE, _WARMUP_PERIOD_OVERRIDE, DataFetcher


def _make_ohlcv(n: int) -> pd.DataFrame:
    dates = pd.date_range(end=date.today(), periods=n, freq="B")
    close = np.linspace(100, 110, n)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(n, 500_000.0),
        },
        index=dates,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    """DataFetcher's in-memory cache is a module-level dict — clear it so
    tests don't leak state into each other via the (symbol, period) key."""
    _MEM_CACHE.clear()
    yield
    _MEM_CACHE.clear()


def _mock_ticker(bars: int) -> MagicMock:
    ticker = MagicMock()
    ticker.history.return_value = _make_ohlcv(bars)
    return ticker


class TestWarmupOverride:
    def test_3mo_request_fetches_1y_from_yfinance(self):
        """A "3mo" request must ask yfinance for the warmup-satisfying period,
        not literally "3mo" (~63 bars) — that's the exact shortfall that let
        SMA_200 collapse to a 63-bar mean in the 2026-09-01 scan."""
        ticker = _mock_ticker(252)
        with patch("signals_app.data.fetcher.yf.Ticker", return_value=ticker):
            result = DataFetcher().fetch("AAPL", "3mo")

        called_period = ticker.history.call_args.kwargs["period"]
        assert called_period == _WARMUP_PERIOD_OVERRIDE["3mo"]
        assert called_period != "3mo"
        assert len(result.df) == 252

    def test_returned_result_reports_originally_requested_period(self):
        """Callers asked for "3mo" and must get "3mo" back — the widening is
        an internal implementation detail, not an interface change."""
        ticker = _mock_ticker(252)
        with patch("signals_app.data.fetcher.yf.Ticker", return_value=ticker):
            result = DataFetcher().fetch("AAPL", "3mo")

        assert result.period == "3mo"
        assert result.bar_count == len(result.df)

    def test_cache_key_uses_requested_period_not_warmup_period(self):
        """A second fetch for the same (symbol, requested period) must be a
        cache hit — the cache key must not fork on the internal fetch_period."""
        ticker = _mock_ticker(252)
        with patch("signals_app.data.fetcher.yf.Ticker", return_value=ticker) as ctor:
            fetcher = DataFetcher()
            first = fetcher.fetch("AAPL", "3mo")
            second = fetcher.fetch("AAPL", "3mo")

        assert ctor.call_count == 1, "second fetch should be served from cache"
        assert first.from_cache is False
        assert second.from_cache is True
        assert len(second.df) == 252

    def test_intraday_period_is_not_widened(self):
        """Intraday periods (15m/1h/4h) must be fetched as requested — yfinance
        limits how far back intraday history is available, so widening those
        the same way as daily periods either fails or grossly over-fetches."""
        ticker = _mock_ticker(50)
        with patch("signals_app.data.fetcher.yf.Ticker", return_value=ticker):
            DataFetcher().fetch("AAPL", "1h")

        called_period = ticker.history.call_args.kwargs["period"]
        assert called_period == "1h"
        assert "1h" not in _WARMUP_PERIOD_OVERRIDE

    def test_long_daily_period_is_left_unwidened(self):
        """A period already long enough (e.g. "1y") should not be overridden
        to something else — it already clears the warmup floor."""
        assert "1y" not in _WARMUP_PERIOD_OVERRIDE

        ticker = _mock_ticker(252)
        with patch("signals_app.data.fetcher.yf.Ticker", return_value=ticker):
            DataFetcher().fetch("AAPL", "1y")

        called_period = ticker.history.call_args.kwargs["period"]
        assert called_period == "1y"
