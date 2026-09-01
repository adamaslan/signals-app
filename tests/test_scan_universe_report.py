"""Tests for scripts/scan_universe_report.py — the rich universe-scan report.

Exercises the pure aggregation + rendering paths on hand-built SymbolReport
objects (no network, no yfinance, no Supabase). The one function that does I/O
(_scan_one) is covered indirectly by test_scan_universe.py's coverage of the
same layers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.scan_universe_report import (  # noqa: E402
    ALL_CATEGORIES,
    SymbolReport,
    _aggregate,
    _blank_breakdown,
    _explain_gate,
    _gate_bucket,
    render_html,
    render_markdown,
)
from signals_app.config import (  # noqa: E402
    PUBLISH_MIN_CONFLUENCE_SCORE,
    PUBLISH_MIN_DATA_QUALITY,
    PUBLISH_MIN_SIGNALS,
)


def _published(ticker: str, score: float) -> SymbolReport:
    bd = _blank_breakdown()
    bd["MACD"] = {"total": 3, "bull": 3, "bear": 0}
    bd["RSI"] = {"total": 1, "bull": 0, "bear": 1}
    return SymbolReport(
        ticker=ticker, ok=True, published=True, bars=64, close=100.0,
        data_quality=1.0, confluence_score=score,
        bias="bullish" if score > 0 else "bearish",
        action="BUY" if score > 0 else "SELL", confidence_label="HIGH",
        bull_count=6, bear_count=2, neutral_count=0, total_signals=8,
        category_breakdown=bd,
        top_signals=[{"signal": "GOLDEN CROSS", "description": "d",
                      "strength": "STRONG BULLISH", "category": "MA_CROSS"}],
    )


def _gated(ticker: str) -> SymbolReport:
    bd = _blank_breakdown()
    bd["RANGE"] = {"total": 2, "bull": 2, "bear": 0}
    return SymbolReport(
        ticker=ticker, ok=True, published=False,
        gate_reason="|confluence| 0.100 < 0.35 (too neutral)",
        bars=64, close=50.0, data_quality=1.0, confluence_score=0.1,
        bias="neutral", action="HOLD", confidence_label="LOW",
        bull_count=2, bear_count=2, neutral_count=1, total_signals=5,
        category_breakdown=bd, top_signals=[],
    )


def _failed(ticker: str) -> SymbolReport:
    return SymbolReport(ticker=ticker, ok=False, published=False,
                        gate_reason="error", error="boom")


# ---------------------------------------------------------------------------
# _explain_gate / _gate_bucket
# ---------------------------------------------------------------------------
class TestGateExplanation:
    def test_low_data_quality_named_first(self):
        assert _explain_gate(0.5, 10, 0.9).startswith("data_quality")

    def test_too_few_signals(self):
        got = _explain_gate(1.0, PUBLISH_MIN_SIGNALS - 1, 0.9)
        assert "signals" in got

    def test_too_neutral(self):
        got = _explain_gate(1.0, 10, 0.0)
        assert got.startswith("|confluence|") and "too neutral" in got

    def test_bucket_collapses_confluence_values_to_one_key(self):
        a = _gate_bucket("|confluence| 0.231 < 0.35 (too neutral)")
        b = _gate_bucket("|confluence| 0.101 < 0.35 (too neutral)")
        assert a == b
        assert str(PUBLISH_MIN_CONFLUENCE_SCORE) in a

    def test_bucket_collapses_data_quality(self):
        assert _gate_bucket("data_quality 0.42 < 0.7") == _gate_bucket(
            "data_quality None < 0.7"
        )

    def test_bucket_passthrough_for_unknown(self):
        assert _gate_bucket("insufficient_bars") == "insufficient_bars"
        assert _gate_bucket(None) == "gated"


# ---------------------------------------------------------------------------
# _aggregate
# ---------------------------------------------------------------------------
class TestAggregate:
    def test_counts(self):
        reports = [
            _published("AAA", 0.6), _published("BBB", -0.7),
            _gated("CCC"), _failed("DDD"),
        ]
        u = _aggregate(reports, period="3mo", elapsed=1.2, requested=4)
        assert u.symbols_scanned == 4
        assert u.symbols_ok == 3
        assert u.symbols_failed == 1
        assert u.symbols_published == 2
        assert u.symbols_gated == 1

    def test_bias_and_gate_distributions(self):
        reports = [_published("AAA", 0.6), _gated("CCC"), _gated("EEE")]
        u = _aggregate(reports, "3mo", 0.5, 3)
        assert u.bias_distribution.get("bullish") == 1
        assert sum(u.gate_reason_distribution.values()) == 2

    def test_category_stats_cover_every_category(self):
        u = _aggregate([_published("AAA", 0.6)], "3mo", 0.1, 1)
        assert set(u.category_stats) == set(ALL_CATEGORIES)
        assert u.category_stats["MACD"]["symbols_firing"] == 1
        assert u.category_stats["MACD"]["fire_rate_pct"] == 100.0
        assert u.category_stats["ADX"]["symbols_firing"] == 0

    def test_strongest_lists_sorted_and_signed(self):
        reports = [
            _published("BULL1", 0.8), _published("BULL2", 0.4),
            _published("BEAR1", -0.9), _published("BEAR2", -0.3),
        ]
        u = _aggregate(reports, "3mo", 0.1, 4)
        assert u.strongest_bullish[0]["ticker"] == "BULL1"
        assert all(r["confluence"] > 0 for r in u.strongest_bullish)
        assert u.strongest_bearish[0]["ticker"] == "BEAR1"
        assert all(r["confluence"] < 0 for r in u.strongest_bearish)

    def test_failures_captured(self):
        u = _aggregate([_failed("XXX")], "3mo", 0.1, 1)
        assert u.failures == [{"ticker": "XXX", "reason": "boom"}]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
class TestRenderers:
    @pytest.fixture
    def universe(self):
        reports = [
            _published("AAA", 0.6), _published("BBB", -0.7),
            _gated("CCC"), _failed("DDD"),
        ]
        return _aggregate(reports, "3mo", 1.0, 4)

    def test_markdown_has_sections_and_symbols(self, universe):
        md = render_markdown(universe)
        assert "# Universe Signal Scan" in md
        assert "## Category firing across the universe" in md
        assert "### AAA — ✅ PUBLISHED" in md
        assert "### DDD — ❌ FAILED" in md
        assert "GOLDEN CROSS" in md

    def test_html_is_self_contained_and_escaped(self, universe):
        h = render_html(universe)
        assert h.startswith("<!doctype html>")
        assert "http://" not in h and "https://" not in h  # no external assets
        assert "<style>" in h
        assert "AAA" in h and "PUBLISHED" in h

    def test_html_escapes_error_text(self):
        r = SymbolReport(ticker="EVIL", ok=False, published=False,
                         error="<script>alert(1)</script>")
        u = _aggregate([r], "3mo", 0.1, 1)
        h = render_html(u)
        assert "<script>alert(1)</script>" not in h
        assert "&lt;script&gt;" in h

    def test_json_payload_round_trips(self, universe):
        from dataclasses import asdict
        blob = json.dumps(asdict(universe), default=str)
        back = json.loads(blob)
        assert back["symbols_published"] == 2
        assert len(back["symbols"]) == 4
