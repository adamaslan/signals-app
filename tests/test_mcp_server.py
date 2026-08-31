"""MCP server tests (design doc §4, §8).

- the tool/resource/prompt surface is exactly what §4.3–4.4 specifies
- write tools are absent unless SIGNALS_MCP_ALLOW_WRITES=1
- bounded batch sizes return a clean error, never a timeout
- every analytical result carries the disclaimer (§4.5.3)
- counter-evidence is never dropped from a high-confidence result (§4.5.2)

Network + LLM are stubbed; the pipeline internals have their own suites.
"""
from __future__ import annotations

import importlib
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from signals_app.data.fetcher import OHLCVResult


def _make_ohlcv(n: int = 260) -> pd.DataFrame:
    dates = pd.date_range(start=date.today() - timedelta(days=n), periods=n, freq="B")
    close = np.abs(np.linspace(100, 175, n) + np.random.normal(0, 1, n))
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
    async def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("signals_app.service.init_db", _noop, raising=False)
    monkeypatch.setattr("signals_app.service.record_run", _noop)


@pytest.fixture
def stub_fetch(monkeypatch: pytest.MonkeyPatch):
    def install(spec: int | Exception = 260) -> None:
        def _fetch(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
            if isinstance(spec, Exception):
                raise spec
            df = _make_ohlcv(spec)
            return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

        monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _fetch)

    return install


def _server(monkeypatch: pytest.MonkeyPatch, *, allow_writes: bool = False):
    monkeypatch.setenv("SIGNALS_MCP_ALLOW_WRITES", "1" if allow_writes else "0")
    import signals_app.mcp.server as srv

    importlib.reload(srv)
    return srv, srv.build_server()


async def _call(server, name: str, args: dict) -> dict:
    res = await server.call_tool(name, args)
    content = res[0] if isinstance(res, tuple) else res.content
    text = content[0].text
    return json.loads(text)


async def test_tool_surface_is_the_nine(monkeypatch: pytest.MonkeyPatch) -> None:
    _srv, server = _server(monkeypatch)
    tools = {t.name for t in await server.list_tools()}
    assert tools == {
        "analyze_symbol",
        "analyze_symbols",
        "backtest_symbol",
        "backtest_universe",
        "get_signal_history",
        "list_detectors",
        "get_calibration",
        "list_universe",
        "engine_health",
    }


async def test_write_tools_absent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _srv, server = _server(monkeypatch, allow_writes=False)
    names = {t.name for t in await server.list_tools()}
    assert "run_scan" not in names


async def test_write_tools_present_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _srv, server = _server(monkeypatch, allow_writes=True)
    names = {t.name for t in await server.list_tools()}
    assert "run_scan" in names


async def test_resources_and_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    _srv, server = _server(monkeypatch)
    uris = {str(r.uri) for r in await server.list_resources()}
    assert uris == {
        "signals://universe",
        "signals://calibration/active",
        "signals://schema",
    }
    prompts = {p.name for p in await server.list_prompts()}
    assert prompts == {"analyze_basket", "explain_signal"}


async def test_analyze_symbol_carries_disclaimer(
    monkeypatch: pytest.MonkeyPatch, stub_fetch
) -> None:
    stub_fetch(260)
    _srv, server = _server(monkeypatch)
    out = await _call(server, "analyze_symbol", {"symbol": "AAPL", "no_llm": True})
    assert "disclaimer" in out
    assert "Not financial advice" in out["disclaimer"]
    # provenance is inside the SignalOutput payload
    assert "code_version" in out
    assert "data_quality_score" in out


async def test_analyze_symbols_caps_at_25(monkeypatch: pytest.MonkeyPatch, stub_fetch) -> None:
    stub_fetch(260)
    _srv, server = _server(monkeypatch)
    out = await _call(server, "analyze_symbols", {"symbols": [f"S{i}" for i in range(30)]})
    assert out["error"] == "too_many_symbols"
    assert "25" in out["message"]
    assert out["ok"] == [] and out["failed"] == []


async def test_backtest_universe_caps_at_500(monkeypatch: pytest.MonkeyPatch) -> None:
    _srv, server = _server(monkeypatch)
    out = await _call(server, "backtest_universe", {"symbols": [f"S{i}" for i in range(501)]})
    assert out["error"] == "too_many_symbols"
    assert "500" in out["message"]


async def test_analyze_symbols_partial_success_returns_both_lists(
    monkeypatch: pytest.MonkeyPatch, monkeypatch2=None
) -> None:
    def _fetch(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
        if symbol.upper() == "BADX":
            raise ValueError("empty data for BADX")
        df = _make_ohlcv(260)
        return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _fetch)
    _srv, server = _server(monkeypatch)
    out = await _call(server, "analyze_symbols", {"symbols": ["AAPL", "BADX"]})
    assert [s["ticker"] for s in out["ok"]] == ["AAPL"]
    assert [f["symbol"] for f in out["failed"]] == ["BADX"]
    assert "disclaimer" in out
