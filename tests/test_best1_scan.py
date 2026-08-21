# use mamba activate signals-app

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from signals_app.db.supabase import EngineRun, SignalRecord


@dataclass
class FakeWriter:
    runs_started: list[tuple[str, str]] = field(default_factory=list)
    runs_finished: list[dict] = field(default_factory=list)
    signals_written: list[SignalRecord] = field(default_factory=list)
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
        self,
        run: EngineRun,
        symbols_total: int,
        symbols_ok: int,
        symbols_failed: int,
        llm_provider,
        status: str,
        error=None,
    ) -> None:
        self.runs_finished.append({
            "run_id": run.id,
            "symbols_total": symbols_total,
            "symbols_ok": symbols_ok,
            "symbols_failed": symbols_failed,
            "status": status,
        })

    def write_signal(self, run: EngineRun, record: SignalRecord) -> None:
        self.signals_written.append(record)

    def write_detector_hits(self, ticker: str, bar_ts: str, signals: list) -> None:
        return None


def test_best1_scan_uses_app_writer_contract(monkeypatch):
    import scripts.best1_scan as best1_scan

    writer = FakeWriter()

    def fake_evaluate_symbol(**kwargs):
        ticker = kwargs["ticker"]
        if ticker == "AAPL":
            return {
                "ticker": "AAPL",
                "published": True,
                "confluence_score": 0.82,
                "direction": "bullish",
                "confidence": 0.81,
                "record": SignalRecord(
                    ticker="AAPL",
                    period="3mo",
                    bar_ts="2026-08-20T00:00:00Z",
                    direction="bullish",
                    confidence=0.81,
                    confluence_score=0.82,
                    bias="bullish",
                    bull_count=5,
                    bear_count=1,
                    total_signals=6,
                    data_quality_score=0.9,
                    data_quality_reasons=[],
                    evidence=[],
                    counter_evidence=[],
                    matrix=None,
                    ai_degraded=False,
                    no_llm=True,
                    prompt_version=None,
                    llm_model=None,
                ),
            }
        if ticker == "MSFT":
            return {
                "ticker": "MSFT",
                "published": True,
                "confluence_score": 0.61,
                "direction": "bullish",
                "confidence": 0.76,
                "record": SignalRecord(
                    ticker="MSFT",
                    period="3mo",
                    bar_ts="2026-08-20T00:00:00Z",
                    direction="bullish",
                    confidence=0.76,
                    confluence_score=0.61,
                    bias="bullish",
                    bull_count=4,
                    bear_count=1,
                    total_signals=5,
                    data_quality_score=0.92,
                    data_quality_reasons=[],
                    evidence=[],
                    counter_evidence=[],
                    matrix=None,
                    ai_degraded=False,
                    no_llm=True,
                    prompt_version=None,
                    llm_model=None,
                ),
            }
        return {"ticker": ticker, "published": False, "record": None}

    monkeypatch.setattr(best1_scan, "evaluate_symbol", fake_evaluate_symbol)

    results = best1_scan.run_best1_scan(
        symbols=["AAPL", "MSFT", "NVDA"],
        writer=writer,
        top_n=1,
        trigger="manual",
        dry_run=False,
    )

    assert [r["ticker"] for r in results] == ["AAPL"]
    assert len(writer.signals_written) == 1
    assert writer.signals_written[0].ticker == "AAPL"
    assert writer.runs_started
    assert writer.runs_finished
