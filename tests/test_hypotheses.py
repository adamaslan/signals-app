"""Backtest hypotheses: suggestion rules, Wilson bounds, verdicts, and the
engine's new by_signal / baseline / bullish-mix outputs they rely on."""
from __future__ import annotations

import pandas as pd
import pytest

from backtests.engine import (
    BASELINE_UP_KEY,
    HitRateBucket,
    bucket_baseline,
    merge_hit_rate_buckets,
    score_historical_signals,
)
from signals_app.detection.base import MutableSignal
from signals_app.detection.historical import BarSignals
from signals_app.hypotheses import (
    MIN_VERDICT_SAMPLES,
    HypothesisFocus,
    evaluate_hypothesis,
    suggest_hypotheses,
    wilson_interval,
)


def _sig(name: str, strength: str, category: str = "MA_CROSS") -> MutableSignal:
    return MutableSignal(signal=name, description="", strength=strength, category=category)


# -- engine additions -------------------------------------------------------
def test_score_tracks_signal_bucket_bullish_mix_and_up_baseline() -> None:
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    df = pd.DataFrame({"Close": [100.0, 110.0, 105.0, 120.0]}, index=idx)
    bars = [
        BarSignals(idx[0], 100.0, [_sig("GOLDEN CROSS", "BULLISH")], False),  # +10% hit
        BarSignals(idx[1], 110.0, [_sig("DEATH CROSS", "BEARISH")], False),  # -4.5% hit
        BarSignals(idx[2], 105.0, [_sig("GOLDEN CROSS", "BULLISH")], False),  # +14% hit
    ]
    out = score_historical_signals(df, bars, horizon_days=1)

    by_signal = {b.key: b for b in out["by_signal"]}
    assert by_signal["GOLDEN CROSS"].hits == 2 and by_signal["GOLDEN CROSS"].bullish == 2
    assert by_signal["DEATH CROSS"].bullish == 0
    (base,) = out["baseline"]
    assert (base.key, base.hits, base.total) == (BASELINE_UP_KEY, 2, 3)


def test_bucket_baseline_weights_by_direction_mix() -> None:
    all_bull = HitRateBucket("X", hits=0, total=10, bullish=10)
    mixed = HitRateBucket("Y", hits=0, total=10, bullish=5)
    assert bucket_baseline(all_bull, 0.6) == pytest.approx(0.6)
    assert bucket_baseline(mixed, 0.6) == pytest.approx(0.5)


def test_merge_sums_bullish_counts() -> None:
    merged = merge_hit_rate_buckets(
        [[HitRateBucket("A", 1, 2, bullish=2)], [HitRateBucket("A", 3, 4, bullish=1)]]
    )
    assert merged == [HitRateBucket("A", 4, 6, bullish=3)]


# -- stats ------------------------------------------------------------------
def test_wilson_interval_brackets_point_estimate() -> None:
    lo, hi = wilson_interval(60, 100)
    assert lo < 0.6 < hi
    assert wilson_interval(0, 0) == (0.0, 1.0)


# -- suggestion -------------------------------------------------------------
def test_cluster_suggested_when_signal_fires_on_multiple_tickers() -> None:
    latest = {
        "AAPL": [_sig("GOLDEN CROSS", "BULLISH")],
        "MSFT": [_sig("GOLDEN CROSS", "BULLISH")],
        "NVDA": [_sig("RSI OVERSOLD", "BULLISH", "RSI")],
    }
    hyps = suggest_hypotheses(latest)
    cluster = [h for h in hyps if h.kind == "cluster"]
    assert len(cluster) == 1
    assert cluster[0].symbols == ("AAPL", "MSFT")
    assert cluster[0].focus == (HypothesisFocus("signal", "GOLDEN CROSS"),)
    assert cluster[0].horizon_days == 20  # MA_CROSS → trend horizon


def test_conflict_and_strength_ladder_suggested() -> None:
    latest = {
        "TSLA": [
            _sig("MACD BULL CROSS", "STRONG BULLISH", "MACD"),
            _sig("RSI OVERBOUGHT", "BEARISH", "RSI"),
        ]
    }
    kinds = {h.kind for h in suggest_hypotheses(latest)}
    assert {"conflict", "strength", "single"} <= kinds


def test_neutral_only_signals_yield_nothing_and_ids_are_stable() -> None:
    assert suggest_hypotheses({"SPY": [_sig("PRICE INSIDE KUMO", "NEUTRAL")]}) == []
    latest = {"A": [_sig("GOLDEN CROSS", "BULLISH")], "B": [_sig("GOLDEN CROSS", "BULLISH")]}
    assert [h.id for h in suggest_hypotheses(latest)] == [h.id for h in suggest_hypotheses(latest)]


def test_horizon_override_and_cap() -> None:
    latest = {t: [_sig("GOLDEN CROSS", "STRONG BULLISH")] for t in "ABCDE"}
    hyps = suggest_hypotheses(latest, horizon_days=7, max_suggestions=2)
    assert len(hyps) == 2
    assert all(h.horizon_days == 7 for h in hyps)


# -- verdicts ---------------------------------------------------------------
def _buckets(bucket: HitRateBucket) -> dict[str, list[HitRateBucket]]:
    return {"signal": [bucket], "category": [], "strength": []}


def test_verdict_supported_when_lower_bound_clears_chance() -> None:
    b = HitRateBucket("GOLDEN CROSS", hits=80, total=100, bullish=100)
    v = evaluate_hypothesis([HypothesisFocus("signal", "GOLDEN CROSS")], _buckets(b), 0.55)
    assert v.status == "supported"


def test_verdict_contradicted_when_upper_bound_below_chance() -> None:
    b = HitRateBucket("GOLDEN CROSS", hits=30, total=100, bullish=100)
    v = evaluate_hypothesis([HypothesisFocus("signal", "GOLDEN CROSS")], _buckets(b), 0.55)
    assert v.status == "contradicted"


def test_verdict_inconclusive_when_thin_and_no_data_when_missing() -> None:
    thin = HitRateBucket("GOLDEN CROSS", hits=MIN_VERDICT_SAMPLES - 1, total=MIN_VERDICT_SAMPLES - 1, bullish=1)
    focus = [HypothesisFocus("signal", "GOLDEN CROSS")]
    assert evaluate_hypothesis(focus, _buckets(thin), 0.5).status == "inconclusive"
    assert evaluate_hypothesis([HypothesisFocus("signal", "NOPE")], _buckets(thin), 0.5).status == "no_data"


def test_multi_focus_one_side_clearing_chance_is_not_a_comparative_win() -> None:
    good = HitRateBucket("A", hits=80, total=100, bullish=100)
    meh = HitRateBucket("B", hits=52, total=100, bullish=0)
    buckets = {"signal": [good, meh], "category": [], "strength": []}
    v = evaluate_hypothesis(
        [HypothesisFocus("signal", "B"), HypothesisFocus("signal", "A")], buckets, 0.5
    )
    assert v.status == "inconclusive"
    assert [f.status for f in v.focuses] == ["inconclusive", "supported"]
    assert "Per-focus evidence only" in v.message
    assert "A cleared chance" in v.message


def test_multi_focus_headline_supported_only_when_every_focus_agrees() -> None:
    a = HitRateBucket("A", hits=80, total=100, bullish=100)
    b = HitRateBucket("B", hits=78, total=100, bullish=100)
    buckets = {"signal": [a, b], "category": [], "strength": []}
    v = evaluate_hypothesis(
        [HypothesisFocus("signal", "A"), HypothesisFocus("signal", "B")], buckets, 0.5
    )
    assert v.status == "supported"


def test_specs_trimmed_to_max_symbols() -> None:
    latest = {f"T{i:02d}": [_sig("GOLDEN CROSS", "BULLISH")] for i in range(10)}
    (cluster,) = [h for h in suggest_hypotheses(latest, max_symbols=4) if h.kind == "cluster"]
    assert len(cluster.symbols) == 4
    assert "first 4 of 10" in cluster.rationale


def test_clusters_capped_and_kinds_interleaved() -> None:
    names = [f"SIG{i}" for i in range(6)]
    latest = {t: [_sig(n, "BULLISH") for n in names] for t in ("A", "B")}
    latest["A"].append(_sig("RSI OVERBOUGHT", "BEARISH", "RSI"))
    hyps = suggest_hypotheses(latest, max_suggestions=20)
    assert sum(h.kind == "cluster" for h in hyps) == 3
    first_kinds = [h.kind for h in hyps[:3]]
    assert len(set(first_kinds)) == 3
