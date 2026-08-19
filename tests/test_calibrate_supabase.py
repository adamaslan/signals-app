"""Tests for score_hits_against_returns() — the pure aggregation logic in
db/calibration_store.py. No live Supabase project required.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signals_app.db.calibration_store import (  # noqa: E402
    DetectorHitRow,
    score_hits_against_returns,
)


class TestScoreHitsAgainstReturns:
    def test_bullish_hit_with_positive_return_scores_as_hit(self) -> None:
        hits = [
            DetectorHitRow(ticker="AAPL", bar_ts="t1", strength="STRONG BULLISH", category="MACD")
        ]
        returns = {("AAPL", "t1"): 0.05}
        result = score_hits_against_returns(hits, returns)
        assert result["strength"]["STRONG BULLISH"] == (1, 1)
        assert result["category"]["MACD"] == (1, 1)
        assert result["confluence_band"]["positive"] == (1, 1)

    def test_bullish_hit_with_negative_return_scores_as_miss(self) -> None:
        hits = [DetectorHitRow(ticker="AAPL", bar_ts="t1", strength="BULLISH", category="RSI")]
        returns = {("AAPL", "t1"): -0.03}
        result = score_hits_against_returns(hits, returns)
        assert result["strength"]["BULLISH"] == (0, 1)

    def test_bearish_hit_with_negative_return_scores_as_hit(self) -> None:
        hits = [
            DetectorHitRow(ticker="AAPL", bar_ts="t1", strength="EXTREME BEARISH", category="ADX")
        ]
        returns = {("AAPL", "t1"): -0.10}
        result = score_hits_against_returns(hits, returns)
        assert result["strength"]["EXTREME BEARISH"] == (1, 1)

    def test_neutral_strength_is_not_scored(self) -> None:
        hits = [DetectorHitRow(ticker="AAPL", bar_ts="t1", strength="NEUTRAL", category="RSI")]
        returns = {("AAPL", "t1"): 0.05}
        result = score_hits_against_returns(hits, returns)
        assert result["strength"] == {}

    def test_missing_return_skips_the_hit(self) -> None:
        hits = [DetectorHitRow(ticker="AAPL", bar_ts="t1", strength="BULLISH", category="RSI")]
        result = score_hits_against_returns(hits, {})
        assert result["strength"] == {}

    def test_aggregates_across_multiple_hits_for_same_key(self) -> None:
        hits = [
            DetectorHitRow(ticker="AAPL", bar_ts="t1", strength="BULLISH", category="RSI"),
            DetectorHitRow(ticker="MSFT", bar_ts="t2", strength="BULLISH", category="MACD"),
            DetectorHitRow(ticker="GOOGL", bar_ts="t3", strength="BULLISH", category="RSI"),
        ]
        returns = {("AAPL", "t1"): 0.02, ("MSFT", "t2"): -0.01, ("GOOGL", "t3"): 0.03}
        result = score_hits_against_returns(hits, returns)
        assert result["strength"]["BULLISH"] == (2, 3)
        assert result["category"]["RSI"] == (2, 2)
        assert result["category"]["MACD"] == (0, 1)
