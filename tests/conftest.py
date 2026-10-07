"""Suite-wide fixtures."""
from __future__ import annotations

import pytest

from signals_app import service


@pytest.fixture(autouse=True)
def _no_live_alpaca(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests mock yfinance; a developer's real Alpaca keys must not reroute them to the network."""
    for name in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "GITHUB_ACTIONS", "K_SERVICE", "MODAL_TASK_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fresh_backtest_cache() -> None:
    """The service caches backtests in-process; tests stub fetch differently per case."""
    service._backtest_cache.clear()
