"""The /v1 integration API: typed shapes, batch partial success, briefs, RAG docs, auth."""
from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

from signals_app import service
from signals_app.api.main import app
from signals_app.data.fetcher import OHLCVResult

N_BARS = 600  # enough for a 2y backtest window (warmup + horizon)


def _make_ohlcv(n: int = N_BARS) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.date_range(end=date.today() - timedelta(days=1), periods=n, freq="B")
    close = np.abs(np.linspace(100, 175, n) + rng.normal(0, 2, n))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": rng.integers(100_000, 1_000_000, n),
        },
        index=dates,
    )


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("signals_app.service.init_db", _noop, raising=False)
    monkeypatch.setattr("signals_app.service.record_run", _noop)


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGNALS_API_KEY", raising=False)


@pytest.fixture
def fetch_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub fetch: tickers starting with BAD are unknown; every call is recorded."""
    calls: list[str] = []

    def _fetch(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
        calls.append(f"{symbol}:{period}")
        if symbol.upper().startswith("BAD"):
            raise ValueError(f"No data returned for {symbol}")
        df = _make_ohlcv()
        return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _fetch)
    monkeypatch.setattr(
        "signals_app.data.fetcher.DataFetcher.fetch_daily_history",
        lambda self, symbol, period="10y": _fetch(self, symbol, period).df,
    )
    return calls


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# --- signals ---------------------------------------------------------------


def test_signal_includes_deterministic_state(client: TestClient, fetch_calls: list[str]) -> None:
    body = client.get("/v1/signals/aapl", params={"no_llm": True}).json()
    state = body["state"]
    assert body["ticker"] == "AAPL"
    assert state["as_of"] is not None
    assert -1.0 <= state["confluence_score"] <= 1.0
    assert state["rsi"] is not None and state["adx"] is not None
    assert state["action"] in {"BUY", "SELL", "HOLD"}


def test_legacy_signals_route_also_gains_state(client: TestClient, fetch_calls: list[str]) -> None:
    body = client.get("/signals/AAPL", params={"no_llm": True}).json()
    assert body["state"]["rsi"] is not None


def test_signals_batch_partial_success_is_200(client: TestClient, fetch_calls: list[str]) -> None:
    res = client.post("/v1/signals/batch", json={"symbols": ["AAPL", "BADX", "msft"]})
    assert res.status_code == 200
    body = res.json()
    assert sorted(s["ticker"] for s in body["ok"]) == ["AAPL", "MSFT"]
    assert body["failed"] == [
        {"symbol": "BADX", "error_type": "SymbolNotFound", "message": "No data returned for BADX"}
    ]
    assert body["partial"] is True


def test_llm_batch_is_capped(client: TestClient) -> None:
    res = client.post(
        "/v1/signals/batch", json={"symbols": [f"T{i}" for i in range(11)], "no_llm": False}
    )
    assert res.status_code == 400
    assert res.json()["error"]["type"] == "BatchTooLarge"


def test_batch_over_hard_limit_is_rejected(client: TestClient) -> None:
    res = client.post("/v1/signals/batch", json={"symbols": [f"T{i}" for i in range(51)]})
    assert res.status_code == 422


# --- errors ----------------------------------------------------------------


def test_v1_errors_use_structured_body(client: TestClient, fetch_calls: list[str]) -> None:
    res = client.get("/v1/signals/BADZ", params={"no_llm": True})
    assert res.status_code == 404
    assert res.json() == {
        "error": {"type": "SymbolNotFound", "message": "No data returned for BADZ"}
    }


def test_legacy_errors_keep_detail_shape(client: TestClient, fetch_calls: list[str]) -> None:
    res = client.get("/signals/BADZ", params={"no_llm": True})
    assert res.status_code == 404
    assert "detail" in res.json()


def test_invalid_period_rejected_before_any_fetch(
    client: TestClient, fetch_calls: list[str]
) -> None:
    res = client.get("/v1/signals/AAPL", params={"period": "7y"})
    assert res.status_code == 422
    assert fetch_calls == []


def test_request_id_is_echoed_and_minted(client: TestClient) -> None:
    echoed = client.get("/v1/meta", headers={"X-Request-ID": "portal-abc"})
    assert echoed.headers["X-Request-ID"] == "portal-abc"
    minted = client.get("/v1/meta")
    assert len(minted.headers["X-Request-ID"]) == 32
    assert minted.headers["X-Signals-Code-Version"]


# --- backtest --------------------------------------------------------------


def test_backtest_shape_matches_legacy_and_is_cached(
    client: TestClient, fetch_calls: list[str]
) -> None:
    v1_body = client.get("/v1/backtest/NVDA").json()
    legacy_body = client.get("/backtest/NVDA").json()
    assert v1_body == legacy_body
    assert set(v1_body) == {
        "symbol", "period", "horizon_days", "bars_scanned", "by_category", "by_strength"
    }
    assert fetch_calls.count("NVDA:2y") == 1, "second call must be served from the backtest cache"


def test_backtest_uses_daily_bars_not_weekly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` maps 2y to weekly bars (~105) — too few for the 200-bar warmup."""
    used: list[str] = []

    def _weekly(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
        used.append("fetch")
        df = _make_ohlcv(105)
        return OHLCVResult(symbol, period, df, from_cache=False, bar_count=len(df))

    def _daily(self: object, symbol: str, period: str = "10y") -> pd.DataFrame:
        used.append("fetch_daily_history")
        return _make_ohlcv(N_BARS)

    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _weekly)
    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch_daily_history", _daily)
    result = asyncio.run(service.backtest("NVDA", "2y"))
    assert used == ["fetch_daily_history"]
    assert result.bars_scanned > 0


def test_backtest_batch_merges_and_reports_failures(
    client: TestClient, fetch_calls: list[str]
) -> None:
    body = client.post("/v1/backtest/batch", json={"symbols": ["AAPL", "BADQ"]}).json()
    assert body["symbols_ok"] == ["AAPL"]
    assert body["failed"][0]["symbol"] == "BADQ"
    assert body["by_strength"]


# --- brief + RAG -----------------------------------------------------------


def test_brief_json_and_text(client: TestClient, fetch_calls: list[str]) -> None:
    body = client.get("/v1/brief/AAPL").json()
    assert body["ticker"] == "AAPL"
    assert body["backtest"] is not None
    assert body["omitted"] == []
    assert body["text"].startswith("TICKER AAPL")
    assert "Historical hit-rates" in body["text"]
    assert "RSI" in body["text"]

    text = client.get("/v1/brief/AAPL", params={"format": "text"})
    assert text.headers["content-type"].startswith("text/plain")
    assert text.text == body["text"]


def test_brief_degrades_when_backtest_fails(
    client: TestClient, fetch_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*_a: object, **_k: object) -> None:
        raise service.UpstreamUnavailable("backtest down")

    monkeypatch.setattr("signals_app.service.backtest", _boom)
    body = client.get("/v1/brief/AAPL").json()
    assert body["backtest"] is None
    assert body["omitted"] == ["backtest (UpstreamUnavailable)"]
    assert "Omitted: backtest (UpstreamUnavailable)" in body["text"]


def test_rag_documents_records_shape(client: TestClient, fetch_calls: list[str]) -> None:
    body = client.post("/v1/rag/documents", json={"symbols": ["AAPL", "BADR"]}).json()
    (doc,) = body["documents"]
    assert doc["id"].startswith("signals-app:AAPL:")
    assert doc["metadata"]["ticker"] == "AAPL"
    assert doc["metadata"]["doc_type"] == "signal_brief"
    assert all(v is not None for v in doc["metadata"].values())
    assert all(isinstance(v, str | int | float | bool) for v in doc["metadata"].values())
    assert body["failed"][0]["symbol"] == "BADR"


def test_rag_documents_chroma_shape_is_upsert_kwargs(
    client: TestClient, fetch_calls: list[str]
) -> None:
    body = client.post(
        "/v1/rag/documents", params={"shape": "chroma"}, json={"symbols": ["AAPL", "MSFT"]}
    ).json()
    assert set(body) == {"ids", "documents", "metadatas", "failed"}
    assert len(body["ids"]) == len(body["documents"]) == len(body["metadatas"]) == 2
    assert len(set(body["ids"])) == 2


def test_rag_document_id_is_stable_for_same_bar(fetch_calls: list[str]) -> None:
    b1 = asyncio.run(_brief_for("AAPL"))
    b2 = asyncio.run(_brief_for("AAPL"))
    assert service.brief_to_rag_document(b1).id == service.brief_to_rag_document(b2).id


async def _brief_for(symbol: str) -> service.TickerBrief:
    return await service.brief(symbol, include_backtest=False)


# --- auth ------------------------------------------------------------------


def test_auth_off_by_default(client: TestClient) -> None:
    assert client.get("/v1/meta").json()["auth_required"] is False
    assert client.get("/v1/detectors").status_code == 200


def test_auth_enforced_when_key_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNALS_API_KEY", "s3cret")
    assert client.get("/v1/meta").status_code == 200, "meta stays public"
    assert client.get("/v1/detectors").status_code == 401
    assert client.get("/v1/detectors", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/v1/detectors", headers={"X-API-Key": "s3cret"}).status_code == 200
    bearer = {"Authorization": "Bearer s3cret"}
    assert client.get("/v1/detectors", headers=bearer).status_code == 200
    assert client.get("/v1/detectors").json()["error"]["type"] == "Unauthorized"


def test_legacy_routes_unaffected_by_api_key(
    client: TestClient, fetch_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGNALS_API_KEY", "s3cret")
    assert client.get("/backtest/AAPL").status_code == 200


# --- concurrency -----------------------------------------------------------


def test_analyze_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow fetch must run off-loop, so a batch overlaps instead of serialising."""
    delay = 0.3

    def _slow_fetch(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
        time.sleep(delay)
        df = _make_ohlcv(260)
        return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _slow_fetch)
    monkeypatch.setattr(
        "signals_app.data.fetcher.DataFetcher.fetch_daily_history",
        lambda self, symbol, period="10y": _slow_fetch(self, symbol, period).df,
    )
    started = time.perf_counter()
    result = asyncio.run(service.analyze_many(["A", "B", "C", "D"], no_llm=True, max_concurrent=4))
    elapsed = time.perf_counter() - started
    assert len(result.ok) == 4
    assert elapsed < delay * 3, f"batch serialised: {elapsed:.2f}s for 4×{delay}s fetches"


def test_llm_synthesis_runs_inside_a_running_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """synthesize_single spins its own loop; called on the server loop it used to raise."""
    seen: list[bool] = []

    def _fake_synth(*_a: object, **_k: object) -> object:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(asyncio.sleep(0))
            seen.append(True)
        finally:
            loop.close()
        raise RuntimeError("stop after proving the loop ran")

    monkeypatch.setattr("signals_app.service.synthesize_single", _fake_synth)

    def _fetch(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
        df = _make_ohlcv(260)
        return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _fetch)
    monkeypatch.setattr(
        "signals_app.data.fetcher.DataFetcher.fetch_daily_history",
        lambda self, symbol, period="10y": _fetch(self, symbol, period).df,
    )
    out = asyncio.run(service.analyze("AAPL", no_llm=False))
    assert seen == [True], "the nested event loop must be able to run"
    assert "synthesis_error" in out.feature_unavailable
