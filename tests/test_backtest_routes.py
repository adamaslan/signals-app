"""``POST /backtest/run`` and ``POST /backtest/suggest`` — the frontend's
backtest lab endpoints. Pins request/response shape and guardrails; the
engine itself is stubbed at the per-symbol boundary."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from backtests.engine import HitRateBucket
from signals_app import service
from signals_app.api.main import app
from signals_app.config import MAX_MANUAL_BACKTEST_SYMBOLS
from signals_app.detection.base import MutableSignal


@pytest.fixture
def stub_backtest(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(symbol: str, period: str = "2y", horizon_days: int = 20):  # noqa: ANN202
        if symbol == "BADX":
            raise service.SymbolNotFound("no such ticker")
        return service.BacktestResult(
            symbol=symbol,
            period=period,
            horizon_days=horizon_days,
            bars_scanned=300,
            by_category=[HitRateBucket("MA_CROSS", 40, 50, bullish=50)],
            by_strength=[HitRateBucket("BULLISH", 40, 50, bullish=50)],
            by_signal=[HitRateBucket("GOLDEN CROSS", 40, 50, bullish=50)],
            up_bars=150,
            scored_bars=300,
        )

    monkeypatch.setattr(service, "backtest", _fake)


@pytest.fixture
def stub_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(symbol: str, period: str):  # noqa: ANN202
        if symbol == "BADX":
            raise service.SymbolNotFound("no such ticker")
        return [
            MutableSignal(
                signal="GOLDEN CROSS", description="", strength="BULLISH", category="MA_CROSS"
            )
        ]

    monkeypatch.setattr(service, "_latest_signals", _fake)


def test_run_merges_symbols_and_judges_focus(stub_backtest) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            json={
                "symbols": ["AAPL", "MSFT", "BADX"],
                "horizon_days": 20,
                "focus": [{"group": "signal", "key": "GOLDEN CROSS"}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbols_ok"] == ["AAPL", "MSFT"]
    assert body["symbols_failed"][0]["symbol"] == "BADX"
    assert body["up_rate"] == 0.5
    (gc,) = body["by_signal"]
    assert (gc["hits"], gc["total"], gc["baseline"]) == (80, 100, 0.5)
    assert gc["lower"] > gc["baseline"]
    assert body["verdict"]["status"] == "supported"


def test_run_without_focus_has_no_verdict(stub_backtest) -> None:
    with TestClient(app) as client:
        resp = client.post("/backtest/run", json={"symbols": ["AAPL"]})
    assert resp.status_code == 200
    assert resp.json()["verdict"] is None


def test_run_caps_symbols_and_rejects_bad_period(stub_backtest) -> None:
    with TestClient(app) as client:
        too_many = client.post(
            "/backtest/run",
            json={"symbols": [f"T{i}" for i in range(MAX_MANUAL_BACKTEST_SYMBOLS + 1)]},
        )
        bad = client.post("/backtest/run", json={"symbols": ["AAPL"], "period": "7w"})
    assert too_many.status_code == 422
    assert bad.status_code == 400


def test_suggest_returns_runnable_specs(stub_latest) -> None:
    with TestClient(app) as client:
        resp = client.post("/backtest/suggest", json={"symbols": ["AAPL", "MSFT", "BADX"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbols_ok"] == ["AAPL", "MSFT"]
    assert body["live_signals"]["AAPL"] == ["GOLDEN CROSS"]
    cluster = next(h for h in body["hypotheses"] if h["kind"] == "cluster")
    assert cluster["symbols"] == ["AAPL", "MSFT"]
    assert cluster["focus"] == [{"group": "signal", "key": "GOLDEN CROSS"}]
    assert {"id", "title", "rationale", "period", "horizon_days"} <= cluster.keys()
