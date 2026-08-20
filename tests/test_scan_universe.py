"""Tests for the publication gate and scan_universe orchestration.

Uses an in-memory fake SignalWriter — no live Supabase project required.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.scan_universe import (  # noqa: E402
    apply_shard,
    parse_shard_spec,
    passes_publication_gate,
    scan_one_symbol,
)
from signals_app.config import (  # noqa: E402
    PUBLISH_MIN_CONFLUENCE_SCORE,
    PUBLISH_MIN_DATA_QUALITY,
    PUBLISH_MIN_SIGNALS,
)
from signals_app.db.supabase import EngineRun, SignalRecord  # noqa: E402


@dataclass
class FakeWriter:
    """In-memory SignalWriter fake — records calls instead of hitting a network."""

    runs_started: list[tuple[str, str]] = field(default_factory=list)
    runs_finished: list[dict] = field(default_factory=list)
    signals_written: list[SignalRecord] = field(default_factory=list)
    detector_hits_written: list[tuple[str, str, int]] = field(default_factory=list)
    symbols_ensured: list[str] = field(default_factory=list)
    _next_run_id: int = 1

    def ensure_symbol(self, ticker: str) -> None:
        self.symbols_ensured.append(ticker)

    def start_run(self, trigger: str, git_sha: str) -> EngineRun:
        self.runs_started.append((trigger, git_sha))
        run = EngineRun(id=self._next_run_id, started_at="2026-08-19T00:00:00Z")
        self._next_run_id += 1
        return run

    def finish_run(
        self, run: EngineRun, symbols_total: int, symbols_ok: int, symbols_failed: int,
        llm_provider, status: str, error=None,
    ) -> None:
        self.runs_finished.append({
            "run_id": run.id, "symbols_total": symbols_total, "symbols_ok": symbols_ok,
            "symbols_failed": symbols_failed, "status": status,
        })

    def write_signal(self, run: EngineRun, record: SignalRecord) -> None:
        self.signals_written.append(record)

    def write_detector_hits(self, ticker: str, bar_ts: str, signals: list) -> None:
        self.detector_hits_written.append((ticker, bar_ts, len(signals)))


class TestPublicationGate:
    def test_passes_when_all_thresholds_clear(self) -> None:
        assert passes_publication_gate(
            data_quality_score=PUBLISH_MIN_DATA_QUALITY + 0.1,
            total_signals=PUBLISH_MIN_SIGNALS + 1,
            confluence_score=PUBLISH_MIN_CONFLUENCE_SCORE + 0.1,
            ai_degraded=False,
        )

    def test_passes_on_strong_sell_side_too(self) -> None:
        assert passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=-(PUBLISH_MIN_CONFLUENCE_SCORE + 0.1),
            ai_degraded=False,
        )

    def test_rejects_low_data_quality(self) -> None:
        assert not passes_publication_gate(
            data_quality_score=PUBLISH_MIN_DATA_QUALITY - 0.01,
            total_signals=10,
            confluence_score=0.9,
            ai_degraded=False,
        )

    def test_rejects_missing_data_quality(self) -> None:
        assert not passes_publication_gate(
            data_quality_score=None, total_signals=10, confluence_score=0.9, ai_degraded=False,
        )

    def test_rejects_too_few_signals(self) -> None:
        assert not passes_publication_gate(
            data_quality_score=0.9,
            total_signals=PUBLISH_MIN_SIGNALS - 1,
            confluence_score=0.9,
            ai_degraded=False,
        )

    def test_rejects_weak_confluence_hold_territory(self) -> None:
        assert not passes_publication_gate(
            data_quality_score=0.9, total_signals=10, confluence_score=0.01, ai_degraded=False,
        )

    def test_bullish_direction_gates_positive_only(self) -> None:
        # Strong bullish: passes
        assert passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=PUBLISH_MIN_CONFLUENCE_SCORE + 0.1,
            ai_degraded=False,
            direction="bullish",
        )
        # Strong bearish: fails (even though it would pass without direction)
        assert not passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=-(PUBLISH_MIN_CONFLUENCE_SCORE + 0.1),
            ai_degraded=False,
            direction="bullish",
        )
        # Weak positive: fails (confluence_score below threshold)
        assert not passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=0.1,
            ai_degraded=False,
            direction="bullish",
        )

    def test_bearish_direction_gates_negative_only(self) -> None:
        # Strong bearish: passes
        assert passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=-(PUBLISH_MIN_CONFLUENCE_SCORE + 0.1),
            ai_degraded=False,
            direction="bearish",
        )
        # Strong bullish: fails (even though it would pass without direction)
        assert not passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=PUBLISH_MIN_CONFLUENCE_SCORE + 0.1,
            ai_degraded=False,
            direction="bearish",
        )
        # Weak negative: fails (confluence_score above -threshold)
        assert not passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=-0.1,
            ai_degraded=False,
            direction="bearish",
        )

    def test_none_direction_gates_both_sides(self) -> None:
        # Strong bullish: passes
        assert passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=PUBLISH_MIN_CONFLUENCE_SCORE + 0.1,
            ai_degraded=False,
            direction=None,
        )
        # Strong bearish: passes
        assert passes_publication_gate(
            data_quality_score=0.9,
            total_signals=5,
            confluence_score=-(PUBLISH_MIN_CONFLUENCE_SCORE + 0.1),
            ai_degraded=False,
            direction=None,
        )
        # Weak either side: fails
        assert not passes_publication_gate(
            data_quality_score=0.9, total_signals=5, confluence_score=0.1, ai_degraded=False,
            direction=None,
        )

    def test_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError):
            passes_publication_gate(
                data_quality_score=0.9,
                total_signals=5,
                confluence_score=PUBLISH_MIN_CONFLUENCE_SCORE + 0.1,
                ai_degraded=False,
                direction="bullish ",  # trailing space — not a valid value
            )


class TestScanOneSymbolIsolation:
    def test_bad_ticker_returns_failed_result_not_exception(self) -> None:
        from signals_app.config import get_settings

        writer = FakeWriter()
        result = scan_one_symbol(
            ticker="$$$NOT-A-REAL-TICKER$$$",
            period="3mo",
            writer=writer,
            run=None,
            settings=get_settings(),
            dry_run=True,
        )
        assert result.ok is False
        assert result.published is False
        assert result.reason is not None
        # No exception propagated — this is the whole point of the isolation.


class TestShardSpec:
    def test_parses_valid_spec(self) -> None:
        assert parse_shard_spec("0/4") == (0, 4)
        assert parse_shard_spec("3/4") == (3, 4)

    def test_rejects_index_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="0 <= INDEX < TOTAL"):
            parse_shard_spec("4/4")
        with pytest.raises(ValueError, match="0 <= INDEX < TOTAL"):
            parse_shard_spec("-1/4")

    def test_rejects_unparseable_spec(self) -> None:
        with pytest.raises(ValueError, match="INDEX/TOTAL"):
            parse_shard_spec("garbage")
        with pytest.raises(ValueError, match="INDEX/TOTAL"):
            parse_shard_spec("1/2/3")


class TestApplyShard:
    def test_four_shards_partition_the_full_list_with_no_overlap_or_gaps(self) -> None:
        symbols = [f"T{i:03d}" for i in range(23)]  # deliberately not divisible by 4
        shards = [apply_shard(symbols, i, 4) for i in range(4)]
        reunited = sorted(s for shard in shards for s in shard)
        assert reunited == symbols
        # Every symbol appears in exactly one shard.
        assert sum(len(shard) for shard in shards) == len(symbols)

    def test_single_shard_returns_everything(self) -> None:
        symbols = ["AAPL", "MSFT", "GOOGL"]
        assert apply_shard(symbols, 0, 1) == symbols

    def test_shard_result_is_deterministic_regardless_of_call_order(self) -> None:
        symbols = ["AAPL", "MSFT", "GOOGL", "SPY", "QQQ"]
        first = apply_shard(symbols, 2, 3)
        second = apply_shard(symbols, 2, 3)
        assert first == second
