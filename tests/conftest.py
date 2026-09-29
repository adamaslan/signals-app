"""Suite-wide fixtures."""
from __future__ import annotations

import pytest

from signals_app import service


@pytest.fixture(autouse=True)
def _fresh_backtest_cache() -> None:
    """The service caches backtests in-process; tests stub fetch differently per case."""
    service._backtest_cache.clear()
