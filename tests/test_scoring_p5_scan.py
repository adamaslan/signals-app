"""P5: EV gate, family gate, and the score -> rank -> gate -> publish scan."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals_app import scanner
from signals_app.config import PUBLISH_MIN_DATA_QUALITY
from signals_app.data.fetcher import OHLCVResult
from signals_app.scanner import (
    MarketContext,
    passes_ev_gate,
    passes_publication_gate,
    scan_universe,
)
from signals_app.scoring.features import FEATURE_SETS
from signals_app.scoring.model import LogisticScorer, confidence_label
from signals_app.scoring.probability import IsotonicCalibrator


class TestEvGate:
    def test_needs_edge_and_positive_ev_over_cost(self):
        assert passes_ev_gate(0.9, 0.60, 0.004, delta=0.05)
        assert not passes_ev_gate(0.9, 0.52, 0.004, delta=0.05)  # too close to a coin flip
        assert not passes_ev_gate(0.9, 0.60, 0.0005, delta=0.05)  # EV below the 10 bp cost
        assert not passes_ev_gate(0.9, 0.60, -0.004, delta=0.05)  # EV disagrees with p
        assert not passes_ev_gate(0.9, 0.60, None, delta=0.05)  # no EV head
        assert not passes_ev_gate(PUBLISH_MIN_DATA_QUALITY - 0.1, 0.60, 0.004, delta=0.05)  # bad data

    def test_sell_side_and_direction_filter(self):
        assert passes_ev_gate(0.9, 0.38, -0.004, delta=0.05)
        assert not passes_ev_gate(0.9, 0.38, -0.004, delta=0.05, direction="bullish")
        assert passes_ev_gate(0.9, 0.62, 0.004, delta=0.05, direction="bullish")
        with pytest.raises(ValueError):
            passes_ev_gate(0.9, 0.6, 0.004, delta=0.05, direction="up")


class TestFamilyGate:
    def test_family_count_replaces_signal_count(self):
        args = dict(data_quality_score=0.9, confluence_score=0.5, ai_degraded=False)
        # 40 fan-out signals but only one family: blocked
        assert not passes_publication_gate(total_signals=40, agreeing_families=1, **args)
        # only 2 signals in total, but two independent families: allowed
        assert passes_publication_gate(total_signals=2, agreeing_families=2, **args)
        # legacy behaviour unchanged when not supplied
        assert passes_publication_gate(total_signals=5, **args)


def test_confidence_label_needs_probability_and_analogs():
    assert confidence_label(0.65, 500) == "HIGH"
    assert confidence_label(0.65, 10) == "MEDIUM"  # right edge, too few analogs
    assert confidence_label(0.35, 500) == "HIGH"  # symmetric sell side
    assert confidence_label(0.58, 500) == "MEDIUM"
    assert confidence_label(0.51, 500) == "LOW"


# ── two-phase scan ────────────────────────────────────────────────────────────


def _ohlcv(seed: int, slope: float, n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(slope, 0.01, n)))
    idx = pd.bdate_range(end="2026-09-22", periods=n)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
         "Volume": 1e6 * (1 + rng.random(n))}, index=idx,
    )


def _scorer(delta: float = 0.0) -> LogisticScorer:
    names = FEATURE_SETS["rung2"]
    coef = [0.0] * len(names)
    coef[names.index("dist_sma50_atr")] = 1.0  # stretched-above-average => higher p
    xs = tuple(np.linspace(0.0, 1.0, 21))
    return LogisticScorer(
        feature_names=names,
        mean=tuple([0.0] * len(names)),
        scale=tuple([1.0] * len(names)),
        coef=tuple(coef),
        intercept=0.0,
        horizon_days=20,
        model_version="test-model",
        calibrator=IsotonicCalibrator(xs=xs, ys=xs, counts=tuple([100] * 21)),
        excess_map=IsotonicCalibrator(xs=xs, ys=tuple(np.linspace(-0.02, 0.02, 21))),
        metrics={"publish_delta": delta},
    )


@pytest.fixture
def patched_scan(monkeypatch):
    slopes = {"UP1": 0.004, "UP2": 0.002, "FLAT": 0.0, "DN1": -0.003, "DN2": -0.005}

    def _fetch(self, symbol, period="3mo"):
        df = _ohlcv(abs(hash(symbol)) % 1000, slopes[symbol.upper()])
        return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _fetch)
    monkeypatch.setattr(scanner, "load_strength_hit_rates_from_supabase", lambda: None)
    monkeypatch.setattr(scanner, "load_market_context", lambda settings: MarketContext(None, None))
    return list(slopes)


def test_model_scan_ranks_the_run_and_gates_on_ev(monkeypatch, patched_scan):
    monkeypatch.setattr(scanner, "load_active_scorer", lambda: _scorer(delta=0.0))
    results = {r.ticker: r for r in scan_universe(patched_scan, period="1y", dry_run=True)}
    assert set(results) == set(patched_scan)
    assert all(r.ok for r in results.values())
    ranks = {t: r.rank_pct for t, r in results.items()}
    assert max(ranks.values()) == 100.0 and min(ranks.values()) == 0.0
    # the more stretched-above-average name outranks the falling one
    assert ranks["UP1"] > ranks["DN2"]
    assert all(0.0 <= r.p_outperform <= 1.0 for r in results.values())


def test_model_scan_publishes_less_with_a_wider_delta(monkeypatch, patched_scan):
    monkeypatch.setattr(scanner, "load_active_scorer", lambda: _scorer(delta=0.0))
    open_gate = sum(r.published for r in scan_universe(patched_scan, period="1y", dry_run=True))
    monkeypatch.setattr(scanner, "load_active_scorer", lambda: _scorer(delta=0.49))
    tight_gate = sum(r.published for r in scan_universe(patched_scan, period="1y", dry_run=True))
    assert tight_gate < open_gate or open_gate == 0


def test_no_model_leaves_the_legacy_path_untouched(monkeypatch, patched_scan):
    monkeypatch.setattr(scanner, "load_active_scorer", lambda: None)
    results = scan_universe(patched_scan, period="1y", dry_run=True)
    assert len(results) == len(patched_scan)
    assert all(r.rank_pct is None and r.p_outperform is None for r in results)
