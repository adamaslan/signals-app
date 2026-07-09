"""Tests for the calibration persistence layer and data-quality scoring."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtests.engine import HitRateBucket, merge_hit_rate_buckets
from signals_app.indicators.data_quality import score_data_quality
from signals_app.scoring.calibration import (
    derive_strength_hit_rates,
    load_strength_hit_rates,
    save_strength_hit_rates,
)


def _make_ohlcv(n: int = 60, last_date: date | None = None) -> pd.DataFrame:
    dates = pd.date_range(end=last_date or date.today(), periods=n, freq="B")
    close = np.linspace(100, 110, n)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(n, 500_000),
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# merge_hit_rate_buckets
# ---------------------------------------------------------------------------


def test_merge_hit_rate_buckets_sums_not_averages():
    a = [HitRateBucket(key="BULLISH", hits=8, total=10)]
    b = [HitRateBucket(key="BULLISH", hits=1, total=90)]

    merged = merge_hit_rate_buckets([a, b])

    assert len(merged) == 1
    assert merged[0].hits == 9
    assert merged[0].total == 100
    assert merged[0].hit_rate == 0.09  # dominated by the 90-sample bucket, not a 50/50 average


def test_merge_hit_rate_buckets_handles_disjoint_keys():
    a = [HitRateBucket(key="BULLISH", hits=5, total=10)]
    b = [HitRateBucket(key="BEARISH", hits=3, total=10)]

    merged = merge_hit_rate_buckets([a, b])

    assert {m.key for m in merged} == {"BULLISH", "BEARISH"}


# ---------------------------------------------------------------------------
# derive / save / load strength hit rates
# ---------------------------------------------------------------------------


def test_derive_strength_hit_rates_drops_small_buckets():
    buckets = [
        HitRateBucket(key="BULLISH", hits=40, total=50),
        HitRateBucket(key="RARE_STRENGTH", hits=2, total=3),
    ]

    rates = derive_strength_hit_rates(buckets, min_bucket_size=30)

    assert rates == {"BULLISH": 0.8}


def test_save_then_load_round_trips(tmp_path):
    path = str(tmp_path / "calibration" / "rates.json")
    rates = {"BULLISH": 0.62, "BEARISH": 0.55}

    save_strength_hit_rates(rates, path=path)
    loaded = load_strength_hit_rates(path=path)

    assert loaded == rates


def test_load_missing_file_returns_none(tmp_path):
    assert load_strength_hit_rates(path=str(tmp_path / "does_not_exist.json")) is None


def test_load_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json")

    assert load_strength_hit_rates(path=str(path)) is None


# ---------------------------------------------------------------------------
# data quality scoring
# ---------------------------------------------------------------------------


def test_score_data_quality_perfect_fresh_data_scores_one():
    df = _make_ohlcv(n=60, last_date=date.today())
    result = score_data_quality(df, period="3mo")

    assert result.score == 1.0
    assert result.reasons == []


def test_score_data_quality_flags_insufficient_bars():
    df = _make_ohlcv(n=5, last_date=date.today())
    result = score_data_quality(df, period="1y")  # needs 200 bars

    assert result.score < 1.0
    assert any("insufficient_bars" in r for r in result.reasons)


def test_score_data_quality_flags_stale_data():
    df = _make_ohlcv(n=60, last_date=date.today() - timedelta(days=10))
    result = score_data_quality(df, period="3mo")

    assert result.score < 1.0
    assert any("stale_last_bar" in r for r in result.reasons)


def test_score_data_quality_flags_high_nan_ratio():
    df = _make_ohlcv(n=60, last_date=date.today())
    df.loc[df.index[:10], "Close"] = float("nan")

    result = score_data_quality(df, period="3mo")

    assert result.score < 1.0
    assert any("nan_ratio" in r for r in result.reasons)


def test_score_data_quality_empty_dataframe_scores_zero():
    df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    result = score_data_quality(df, period="3mo")

    assert result.score == 0.0
    assert result.reasons == ["empty_dataframe"]
