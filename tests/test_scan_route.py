"""``POST /scan`` — the manual real-scan trigger a frontend "run scan" button
calls. Thin adapter over ``service.scan``; see ``tests/test_service.py`` for
the underlying scan behavior. This only pins the route's request/response
shape and its guardrails (symbol cap, bad period).
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from signals_app.api.main import app
from signals_app.config import MAX_MANUAL_SCAN_SYMBOLS


@pytest.fixture
def stub_scan(monkeypatch: pytest.MonkeyPatch):
    """Replace scanner.scan_universe — mirrors tests/test_service.py's fixture."""

    def install(published: set[str], failed: set[str]):
        from signals_app import scanner

        def _fake(symbols, **kw):  # noqa: ANN001, ANN003
            prog = kw.get("progress")
            results = []
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

    return install


def test_post_scan_dry_run_returns_outcomes(stub_scan) -> None:
    stub_scan(published={"AAPL"}, failed={"BADX"})
    with TestClient(app) as client:
        resp = client.post(
            "/scan",
            json={"symbols": ["AAPL", "MSFT", "BADX"], "dry_run": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbols_total"] == 3
    assert body["symbols_published"] == 1
    assert body["symbols_failed"] == 1
    assert body["dry_run"] is True
    assert body["trigger"] == "manual"
    assert {o["ticker"] for o in body["outcomes"]} == {"AAPL", "MSFT", "BADX"}


def test_post_scan_bad_period_maps_to_400(stub_scan) -> None:
    stub_scan(published=set(), failed=set())
    with TestClient(app) as client:
        resp = client.post(
            "/scan", json={"symbols": ["AAPL"], "period": "bogus", "dry_run": True}
        )
    assert resp.status_code == 400


def test_post_scan_over_the_symbol_cap_is_rejected() -> None:
    too_many = [f"T{i}" for i in range(MAX_MANUAL_SCAN_SYMBOLS + 1)]
    with TestClient(app) as client:
        resp = client.post("/scan", json={"symbols": too_many, "dry_run": True})
    assert resp.status_code == 422  # pydantic max_length validation


def test_post_scan_empty_symbols_is_rejected() -> None:
    with TestClient(app) as client:
        resp = client.post("/scan", json={"symbols": [], "dry_run": True})
    assert resp.status_code == 422
