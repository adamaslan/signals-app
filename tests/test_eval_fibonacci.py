"""Tests for scripts/eval_fibonacci.py's weekly-resampling and MTF5 helpers.

FIB-ICHIMOKU-MA.md §12.7 step 2 (MTF5/MTF2) and invariants M1 (completed
weekly bars only) and M6 (multi-timeframe features are features, not votes).
Pure-function tests only — no network, no yfinance.

``scripts/`` isn't a package (no ``__init__.py``), so it's imported the same
way ``scripts/calibrate.py`` inserts itself onto ``sys.path`` at runtime.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import eval_fibonacci as ef  # noqa: E402


def _daily_ohlcv(n: int, start: str = "2024-01-02") -> pd.DataFrame:
    """Business-day OHLCV, deterministic close path."""
    dates = pd.date_range(start=start, periods=n, freq="B")
    close = 100.0 + np.cumsum(np.sin(np.arange(n) / 5.0))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


class TestResampleToWeekly:
    """M1: completed weekly bars only, W-FRI grouping."""

    def test_drops_a_forming_final_week(self):
        # 2024-01-02 (Tue) through 2024-01-17 (Wed) — the last week (Mon
        # 2024-01-15 - Wed 2024-01-17) has no Thursday/Friday yet, so it's
        # still forming and must be dropped.
        df = _daily_ohlcv(12)  # 12 business days from 2024-01-02
        assert df.index[-1].date() == date(2024, 1, 17)  # Wed, week not yet closed

        weekly = ef.resample_to_weekly(df)

        assert weekly.index[-1].date() < date(2024, 1, 19)  # last kept week's Friday
        assert weekly.index[-1].date() == date(2024, 1, 12)  # exactly the second completed week
        assert len(weekly) == 2  # both completed weeks kept, none dropped
        last_daily_in_kept_weeks = df.index[df.index <= weekly.index[-1]]
        assert len(last_daily_in_kept_weeks) < len(df)  # the forming week's days were excluded

    def test_keeps_a_week_whose_friday_is_covered(self):
        # 10 business days from a Monday covers exactly two full weeks.
        df = _daily_ohlcv(10, start="2024-01-01")  # Mon 1/1 .. Fri 1/12
        assert df.index[-1].date() == date(2024, 1, 12)  # Fri — week is closed

        weekly = ef.resample_to_weekly(df)

        assert weekly.index[-1].date() == date(2024, 1, 12)
        assert len(weekly) == 2

    def test_weekly_ohlc_aggregates_correctly(self):
        df = _daily_ohlcv(5, start="2024-01-01")  # one full Mon-Fri week
        weekly = ef.resample_to_weekly(df)

        assert len(weekly) == 1
        row = weekly.iloc[0]
        assert row["Open"] == df["Open"].iloc[0]
        assert row["Close"] == df["Close"].iloc[-1]
        assert row["High"] == df["High"].max()
        assert row["Low"] == df["Low"].min()
        assert row["Volume"] == df["Volume"].sum()

    def test_empty_input_returns_empty(self):
        df = _daily_ohlcv(0)
        assert ef.resample_to_weekly(df).empty


class TestExcessMassNear:
    """MTF5: excess mass in a band around 0.618 vs uniform expectation."""

    def test_no_depths_returns_nan_zero_n(self):
        result = ef.excess_mass_near([])
        assert result["n"] == 0
        assert np.isnan(result["excess"])

    def test_all_mass_in_band_gives_positive_excess(self):
        depths = [0.618] * 50
        result = ef.excess_mass_near(depths, center=0.618, half_width=0.066)
        assert result["n"] == 50
        assert result["observed"] == pytest.approx(1.0)
        assert result["excess"] > 0

    def test_uniform_depths_give_excess_near_zero(self):
        # Evenly spaced over [0, 1] should match the uniform-density
        # expectation closely.
        depths = list(np.linspace(0.0, 1.0, 1000))
        result = ef.excess_mass_near(depths, center=0.618, half_width=0.066)
        assert abs(result["excess"]) < 0.02

    def test_out_of_domain_depths_excluded_from_n(self):
        depths = [0.5, 1.5, -0.1, 0.6]  # only 0.5 and 0.6 are in [0, 1]
        result = ef.excess_mass_near(depths)
        assert result["n"] == 2


class TestIntervalConstants:
    """MTF2: weekly interval gets its own horizon/warmup, not the daily default."""

    def test_weekly_horizon_is_13_bars(self):
        assert ef.HORIZON_BY_INTERVAL["1wk"] == 13

    def test_weekly_warmup_is_smaller_than_daily(self):
        assert ef.WARMUP_BY_INTERVAL["1wk"] < ef.WARMUP_BY_INTERVAL["1d"]

    def test_daily_interval_constants_unchanged(self):
        assert ef.HORIZON_BY_INTERVAL["1d"] == ef.HORIZON
        assert ef.WARMUP_BY_INTERVAL["1d"] == ef.WARMUP
