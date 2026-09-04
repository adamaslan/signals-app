"""Tests for ``signals_app.service`` — the one seam (design doc §2, §8).

Network and LLM are stubbed: ``DataFetcher.fetch`` returns synthetic OHLCV,
synthesis runs in ``no_llm`` mode. What's under test is the seam's behavior —
exception translation, partial-batch semantics, the weighted universe merge —
not the pipeline internals (those have their own suites).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from signals_app import service
from signals_app.data.fetcher import OHLCVResult
from signals_app.service import (
    BatchResult,
    InsufficientData,
    InvalidPeriod,
    SymbolNotFound,
)


def _make_ohlcv(n: int = 260) -> pd.DataFrame:
    dates = pd.date_range(start=date.today() - timedelta(days=n), periods=n, freq="B")
    close = np.abs(np.linspace(100, 180, n) + np.random.normal(0, 1, n))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.random.randint(100_000, 1_000_000, n),
        },
        index=dates,
    )


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize persistence — service tests don't exercise the DB."""

    async def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("signals_app.service.init_db", _noop, raising=False)
    monkeypatch.setattr("signals_app.service.record_run", _noop)


@pytest.fixture
def stub_fetch(monkeypatch: pytest.MonkeyPatch):
    """Return a helper that installs a per-symbol synthetic/raising fetcher."""

    def install(bars_by_symbol: dict[str, int | Exception]) -> None:
        def _fetch(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
            spec = bars_by_symbol.get(symbol.upper())
            if isinstance(spec, Exception):
                raise spec
            n = spec if isinstance(spec, int) else 260
            df = _make_ohlcv(n)
            return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

        monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _fetch)

    return install


# ---------------------------------------------------------------------------
# analyze — exception translation
# ---------------------------------------------------------------------------


async def test_analyze_invalid_period_raises_invalid_period() -> None:
    with pytest.raises(InvalidPeriod):
        await service.analyze("AAPL", "not-a-period")


async def test_analyze_unknown_symbol_raises_symbol_not_found(stub_fetch) -> None:
    stub_fetch({"AAPL": ValueError("yfinance returned empty data for AAPL")})
    with pytest.raises(SymbolNotFound):
        await service.analyze("AAPL", "3mo", no_llm=True)


async def test_analyze_too_few_bars_raises_insufficient_data(stub_fetch) -> None:
    stub_fetch({"AAPL": 10})
    with pytest.raises(InsufficientData):
        await service.analyze("AAPL", "3mo", no_llm=True)


async def test_analyze_happy_path_returns_signal_output(stub_fetch) -> None:
    stub_fetch({"AAPL": 260})
    out = await service.analyze("aapl", "3mo", no_llm=True)
    assert out.ticker == "AAPL"
    assert out.code_version is not None
    assert 0.0 <= out.signal.confidence <= 1.0
    assert out.signal.timeframe.value == "3M"


# ---------------------------------------------------------------------------
# analyze_many — partial success is a return value, never an exception (§5)
# ---------------------------------------------------------------------------


async def test_analyze_many_partial_success_puts_bad_symbol_in_failed(stub_fetch) -> None:
    stub_fetch(
        {
            "AAPL": 260,
            "MSFT": 260,
            "BADX": ValueError("yfinance returned empty data for BADX"),
        }
    )
    result: BatchResult = await service.analyze_many(["AAPL", "MSFT", "BADX"], "3mo")
    ok_syms = {o.ticker for o in result.ok}
    assert ok_syms == {"AAPL", "MSFT"}
    assert [f.symbol for f in result.failed] == ["BADX"]
    assert result.failed[0].error_type == "SymbolNotFound"
    assert result.partial is True
    assert result.all_ok is False


async def test_analyze_many_all_ok_sets_all_ok(stub_fetch) -> None:
    stub_fetch({"AAPL": 260, "MSFT": 260})
    result = await service.analyze_many(["AAPL", "MSFT"], "3mo")
    assert result.all_ok is True
    assert result.partial is False


async def test_analyze_many_bad_period_raises_up_front() -> None:
    # A bad period fails every symbol — that's a raise, not a per-symbol failure.
    with pytest.raises(InvalidPeriod):
        await service.analyze_many(["AAPL", "MSFT"], "bogus")


async def test_analyze_many_dedupes_and_normalizes_symbols(stub_fetch) -> None:
    stub_fetch({"AAPL": 260})
    result = await service.analyze_many(["aapl", "AAPL", " aapl "], "3mo")
    assert len(result.ok) == 1


# ---------------------------------------------------------------------------
# backtest_many — the weighted merge, not a mean of means (§2.1)
# ---------------------------------------------------------------------------


async def test_backtest_many_merges_buckets_weighted(stub_fetch) -> None:
    stub_fetch({"AAPL": 400, "MSFT": 400})
    merged = await service.backtest_many(["AAPL", "MSFT"], period="2y", horizon_days=20)
    assert set(merged.symbols_ok) == {"AAPL", "MSFT"}
    assert not merged.symbols_failed
    # Every merged bucket's hit_rate is hits/total of the summed counts.
    for b in [*merged.by_strength, *merged.by_category]:
        assert b.total >= 0
        if b.total:
            assert 0.0 <= b.hit_rate <= 1.0
            assert abs(b.hit_rate - b.hits / b.total) < 1e-9


async def test_backtest_many_partial_keeps_successes(stub_fetch) -> None:
    stub_fetch(
        {
            "AAPL": 400,
            "BADX": ValueError("empty data for BADX"),
        }
    )
    merged = await service.backtest_many(["AAPL", "BADX"], period="2y", horizon_days=20)
    assert merged.symbols_ok == ["AAPL"]
    assert [f.symbol for f in merged.symbols_failed] == ["BADX"]


# ---------------------------------------------------------------------------
# detectors / health
# ---------------------------------------------------------------------------


async def test_detectors_lists_all_registered() -> None:
    infos = await service.detectors()
    names = {d.name for d in infos}
    assert "RSISignalDetector" in names
    assert len(infos) == 19


async def test_health_reports_yfinance_unreachable_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
        raise RuntimeError("network down")

    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _boom)
    report = await service.health()
    assert report.yfinance_ok is False
    assert report.ok is False
    assert "unreachable" in report.detail["yfinance"]


# ---------------------------------------------------------------------------
# scan — the production universe scan (step 4)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_scan(monkeypatch: pytest.MonkeyPatch):
    """Replace scanner.scan_universe with a synthetic one that honors `progress`."""

    def install(published: set[str], failed: set[str]) -> list:
        calls: list[dict] = []
        from signals_app import scanner

        def _fake(symbols: list[str], **kw: object) -> list:
            calls.append({"symbols": list(symbols), **kw})
            results = []
            prog = kw.get("progress")
            for i, t in enumerate(symbols, 1):
                r = scanner.SymbolResult(
                    ticker=t,
                    ok=t not in failed,
                    published=t in published,
                    reason=None if t not in failed else "boom",
                )
                results.append(r)
                if callable(prog):
                    prog(i, len(symbols), r)
            return results

        monkeypatch.setattr("signals_app.scanner.scan_universe", _fake)
        return calls

    return install


async def test_scan_dry_run_never_constructs_a_writer(stub_scan, monkeypatch) -> None:
    def _no_writer(*_a: object, **_k: object) -> object:
        raise AssertionError("SupabaseWriter must not be constructed for a dry run")

    monkeypatch.setattr("signals_app.db.supabase.SupabaseWriter", _no_writer, raising=False)
    stub_scan(published={"AAPL"}, failed=set())
    result = await service.scan(["AAPL", "MSFT"], dry_run=True)
    assert result.dry_run is True
    assert result.symbols_total == 2
    assert result.symbols_published == 1


async def test_scan_reports_progress_per_symbol(stub_scan) -> None:
    stub_scan(published={"AAPL"}, failed={"BADX"})
    ticks: list[tuple[int, int, str]] = []
    result = await service.scan(
        ["AAPL", "MSFT", "BADX"],
        dry_run=True,
        progress=lambda p: ticks.append((p.done, p.total, p.ticker)),
    )
    assert [t[0] for t in ticks] == [1, 2, 3]
    assert all(t[1] == 3 for t in ticks)
    assert result.partial is True  # 1 of 3 failed
    assert result.symbols_failed == 1


async def test_scan_invalid_period_raises() -> None:
    with pytest.raises(InvalidPeriod):
        await service.scan(["AAPL"], period="bogus", dry_run=True)


async def test_scan_no_symbols_raises_symbol_not_found() -> None:
    with pytest.raises(SymbolNotFound):
        await service.scan([], dry_run=True)


async def test_scan_applies_shard_before_scanning(stub_scan) -> None:
    calls = stub_scan(published=set(), failed=set())
    await service.scan(
        ["A", "B", "C", "D", "E", "F"], dry_run=True, shard=(0, 2)
    )
    # sorted + every-2nd-from-0 → A, C, E
    assert calls[0]["symbols"] == ["A", "C", "E"]
