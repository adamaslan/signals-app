"""Alpaca-first routing in the data fetcher (network mocked)."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from signals_app.data import fetcher
from signals_app.data.fetcher import _fetch_ohlcv


def _frame(n: int = 60) -> pd.DataFrame:
    close = np.linspace(100, 110, n)
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": np.full(n, 1e6)},
        index=pd.date_range(end="2026-10-07", periods=n, freq="B"),
    )


@pytest.fixture
def alpaca_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")


def test_daily_period_uses_alpaca_when_configured(alpaca_keys) -> None:
    with patch.object(fetcher.alpaca_md, "daily_bars_frames", return_value={"SPY": _frame()}) as bars, \
            patch.object(fetcher, "_fetch_from_yfinance") as yf_fetch:
        df = _fetch_ohlcv("SPY", "1y")
    assert len(df) == 60
    bars.assert_called_once_with(["SPY"], 375)
    yf_fetch.assert_not_called()


def test_falls_back_to_yfinance_locally_when_alpaca_has_no_bars(alpaca_keys, caplog) -> None:
    with patch.object(fetcher.alpaca_md, "daily_bars_frames", return_value={}), \
            patch.object(fetcher, "_fetch_from_yfinance", return_value=_frame(5)) as yf_fetch:
        df = _fetch_ohlcv("ZZZ", "3mo")
    assert len(df) == 5
    yf_fetch.assert_called_once()
    assert "market-data fallback" in caplog.text


def test_datacenter_host_fails_closed_instead_of_calling_yfinance(alpaca_keys, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with patch.object(fetcher.alpaca_md, "daily_bars_frames", return_value={}), \
            patch.object(fetcher, "_fetch_from_yfinance") as yf_fetch:
        with pytest.raises(ValueError, match="Alpaca could not serve"):
            _fetch_ohlcv("ZZZ", "3mo")
    yf_fetch.assert_not_called()


def test_datacenter_host_without_keys_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "svc")
    with patch.object(fetcher, "_fetch_from_yfinance") as yf_fetch:
        with pytest.raises(ValueError, match="not set on a datacenter host"):
            _fetch_ohlcv("SPY", "1y")
    yf_fetch.assert_not_called()


def test_weekly_interval_stays_on_yfinance_locally(alpaca_keys) -> None:
    with patch.object(fetcher.alpaca_md, "daily_bars_frames") as bars, \
            patch.object(fetcher, "_fetch_from_yfinance", return_value=_frame(5)) as yf_fetch:
        _fetch_ohlcv("SPY", "5y")  # 5y maps to the 1wk interval
    bars.assert_not_called()
    yf_fetch.assert_called_once()
