"""Wire shapes shared by the legacy routes and /v1 — one place per contract."""
from __future__ import annotations

from typing import Any

from signals_app import service


def bucket_to_dict(bucket: Any) -> dict[str, Any]:
    """One hit-rate bucket. Field names are a contract with nuwrrrld-portal's
    ``lib/backtest.ts`` ``isBacktestBucket`` guard — do not rename."""
    return {
        "key": bucket.key,
        "hits": bucket.hits,
        "total": bucket.total,
        "hit_rate": round(bucket.hit_rate, 4),
    }


def backtest_to_dict(result: service.BacktestResult) -> dict[str, Any]:
    """A single-symbol backtest, exactly as ``GET /backtest/{symbol}`` has always returned it."""
    return {
        "symbol": result.symbol,
        "period": result.period,
        "horizon_days": result.horizon_days,
        "bars_scanned": result.bars_scanned,
        "by_category": [bucket_to_dict(b) for b in result.by_category],
        "by_strength": [bucket_to_dict(b) for b in result.by_strength],
    }


def failure_to_dict(failure: service.BatchFailure) -> dict[str, str]:
    """One symbol that failed inside a batch."""
    return {"symbol": failure.symbol, "error_type": failure.error_type, "message": failure.message}


def scan_to_dict(result: service.ScanResult) -> dict[str, Any]:
    """A scan outcome, exactly as ``POST /scan`` has always returned it."""
    return {
        "symbols_total": result.symbols_total,
        "symbols_ok": result.symbols_ok,
        "symbols_failed": result.symbols_failed,
        "symbols_published": result.symbols_published,
        "dry_run": result.dry_run,
        "trigger": result.trigger,
        "elapsed_seconds": round(result.elapsed_seconds, 2),
        "outcomes": [
            {"ticker": o.ticker, "ok": o.ok, "published": o.published, "reason": o.reason}
            for o in result.outcomes
        ],
    }
