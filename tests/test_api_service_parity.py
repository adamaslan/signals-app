"""Parity: the FastAPI adapter returns exactly what the service produces (§8).

``GET /signals/{symbol}`` and ``signals analyze {symbol} --json`` must emit a
byte-identical ``SignalOutput`` payload. Both are thin adapters over
``service.analyze`` — this test pins that by comparing the route's JSON body to
``service.analyze(...).model_dump(mode="json")`` for the same stubbed input.

Also checks the exception → HTTP-status mapping in ``routes._raise_http``.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

from signals_app import service
from signals_app.api.main import app
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
    def install(spec: int | Exception) -> None:
        def _fetch(self: object, symbol: str, period: str = "3mo") -> OHLCVResult:
            if isinstance(spec, Exception):
                raise spec
            df = _make_ohlcv(spec)
            return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

        monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", _fetch)

    return install


def test_get_signals_body_equals_service_output(stub_fetch, monkeypatch) -> None:
    """The route body is exactly ``service.analyze(...).model_dump(mode="json")``.

    The fetcher is seeded once and deterministic across the two calls (same
    stub instance), so the only thing that could differ is the adapter — which
    is what we're asserting is a pass-through.
    """
    seed = 1234
    stub_fetch(260)

    # Route body
    np.random.seed(seed)
    with TestClient(app) as client:
        resp = client.get("/signals/AAPL", params={"period": "3mo", "no_llm": True})
    assert resp.status_code == 200
    route_body = resp.json()

    # Service output for the identical stubbed input
    np.random.seed(seed)
    import asyncio

    svc = asyncio.run(service.analyze("AAPL", "3mo", no_llm=True)).model_dump(mode="json")

    assert route_body == svc


def test_get_signals_unknown_symbol_maps_to_404(stub_fetch) -> None:
    stub_fetch(ValueError("yfinance returned empty data for AAPL"))
    with TestClient(app) as client:
        resp = client.get("/signals/AAPL", params={"no_llm": True})
    assert resp.status_code == 404


def test_get_signals_bad_period_maps_to_400() -> None:
    with TestClient(app) as client:
        resp = client.get("/signals/AAPL", params={"period": "bogus"})
    assert resp.status_code == 400


def test_get_signals_insufficient_data_maps_to_400(stub_fetch) -> None:
    stub_fetch(10)
    with TestClient(app) as client:
        resp = client.get("/signals/AAPL", params={"no_llm": True})
    assert resp.status_code == 400
