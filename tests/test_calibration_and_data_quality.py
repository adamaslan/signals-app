"""Tests for the calibration persistence layer and data-quality scoring."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from backtests.engine import HitRateBucket, merge_hit_rate_buckets
from signals_app.indicators.data_quality import score_data_quality
from signals_app.scoring.calibration import (
    derive_strength_hit_rates,
    load_strength_hit_rates,
    save_strength_hit_rates,
)

# Fixed reference instant for all data-quality tests. Using a real clock
# (date.today() / datetime.now()) here made the stale/fresh assertions
# depend on time-of-day and weekday: `_make_ohlcv(freq="B")` anchors the last
# bar to the nearest business day, so the true gap to "now" varied with when
# the suite happened to run and could cross the 26h staleness threshold.
_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
_TODAY = _NOW.date()


def _make_ohlcv(n: int = 60, last_date: date | None = None) -> pd.DataFrame:
    dates = pd.date_range(end=last_date or _TODAY, periods=n, freq="B")
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


def test_load_picks_up_changes_after_mtime_bump(tmp_path):
    """Cache is keyed on mtime — a rewritten file must not serve stale cached rates."""
    path = str(tmp_path / "rates.json")

    save_strength_hit_rates({"BULLISH": 0.5}, path=path)
    first = load_strength_hit_rates(path=path)

    import os
    import time

    time.sleep(0.01)
    save_strength_hit_rates({"BULLISH": 0.9}, path=path)
    os.utime(path, None)  # ensure mtime actually advances on fast filesystems
    second = load_strength_hit_rates(path=path)

    assert first == {"BULLISH": 0.5}
    assert second == {"BULLISH": 0.9}


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
    df = _make_ohlcv(n=60, last_date=_TODAY)
    result = score_data_quality(df, period="3mo", now=_NOW)

    assert result.score == 1.0
    assert result.reasons == []


def test_score_data_quality_flags_insufficient_bars():
    df = _make_ohlcv(n=5, last_date=_TODAY)
    result = score_data_quality(df, period="1y", now=_NOW)  # needs 200 bars

    assert result.score < 1.0
    assert any("insufficient_bars" in r for r in result.reasons)


def test_score_data_quality_flags_stale_data():
    df = _make_ohlcv(n=60, last_date=_TODAY - timedelta(days=10))
    result = score_data_quality(df, period="3mo", now=_NOW)

    assert result.score < 1.0
    assert any("stale_last_bar" in r for r in result.reasons)


def test_score_data_quality_flags_high_nan_ratio():
    df = _make_ohlcv(n=60, last_date=_TODAY)
    df.loc[df.index[:10], "Close"] = float("nan")

    result = score_data_quality(df, period="3mo", now=_NOW)

    assert result.score < 1.0
    assert any("nan_ratio" in r for r in result.reasons)


def test_score_data_quality_empty_dataframe_scores_zero():
    df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    result = score_data_quality(df, period="3mo", now=_NOW)

    assert result.score == 0.0
    assert result.reasons == ["empty_dataframe"]


def test_score_data_quality_flags_missing_columns():
    df = _make_ohlcv(n=60, last_date=_TODAY).drop(columns=["Volume"])
    result = score_data_quality(df, period="3mo", now=_NOW)

    assert result.score < 1.0
    assert any("missing_columns:Volume" in r for r in result.reasons)


def test_score_data_quality_flags_future_timestamp():
    df = _make_ohlcv(n=60, last_date=_TODAY + timedelta(days=5))
    result = score_data_quality(df, period="3mo", now=_NOW)

    assert result.score < 1.0
    assert any("future_last_bar" in r for r in result.reasons)


def test_score_data_quality_handles_pure_date_index_without_crashing():
    # Pure datetime.date index (no time-of-day) — common for daily bars from
    # some data sources. Must not raise AttributeError on missing .tzinfo.
    df = _make_ohlcv(n=60, last_date=_TODAY)
    df.index = pd.Index([d.date() for d in df.index])

    result = score_data_quality(df, period="3mo", now=_NOW)

    assert result.score == 1.0
    assert "unparsable_last_bar_timestamp" not in " ".join(result.reasons)
