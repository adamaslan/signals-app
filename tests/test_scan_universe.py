"""Tests for the publication gate and scan_universe orchestration.

Uses an in-memory fake SignalWriter — no live Supabase project required.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.scan_universe import passes_publication_gate, scan_one_symbol  # noqa: E402
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
