"""Confidence calibration — loads/derives the strength -> hit-rate table.

ConfluenceRanker.rank_signals() already accepts an optional strength_hit_rates
map and nudges confidence_label toward measured backtest hit-rates when one is
supplied (see scoring/confluence.py). This module is the missing other half:
computing that table from backtests.engine.score_historical_signals() output
and persisting/loading it so the live API path can pass it in.

load_strength_hit_rates() (local JSON file) is the original, still-used-by-
scripts/calibrate.py path for offline/local-dev calibration.
load_strength_hit_rates_from_supabase() (Phase 7) is what
scripts/scan_universe.py calls in production — it reads the `calibration`
table's active generation, which scripts/calibrate_supabase.py maintains.
The JSON-file path doesn't survive a container restart in CI; the Supabase
path does.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from signals_app.config import (
    CALIBRATION_FILE,
    CALIBRATION_MIN_BUCKET_SIZE,
    SUPABASE_REQUEST_TIMEOUT_SECONDS,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)

logger = logging.getLogger(__name__)


def derive_strength_hit_rates(
    by_strength: list,
    min_bucket_size: int = CALIBRATION_MIN_BUCKET_SIZE,
) -> dict[str, float]:
    """Convert backtests.engine's by_strength HitRateBucket list into a rate map.

    Args:
        by_strength: The "by_strength" list returned by score_historical_signals()
            (or several such lists merged bucket-by-bucket — see scripts/calibrate.py).
        min_bucket_size: Buckets with fewer samples than this are dropped —
            a hit-rate from 4 signals is noise, not a calibrated probability.

    Returns:
        Dict of SignalStrength value -> hit-rate (0.0-1.0), trustworthy buckets only.
    """
    return {b.key: b.hit_rate for b in by_strength if b.total >= min_bucket_size}


def save_strength_hit_rates(rates: dict[str, float], path: str = CALIBRATION_FILE) -> None:
    """Persist a strength hit-rate table as JSON.

    Args:
        rates: Output of derive_strength_hit_rates(), possibly merged across symbols.
        path: Destination file path. Parent directories are created if missing.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rates, indent=2, sort_keys=True))
    logger.info("calibration: wrote %d strength buckets to %s", len(rates), dest)


# In-process cache keyed on (path, mtime) — load_strength_hit_rates() is called
# on every GET /signals/{symbol}, so a synchronous read + json.loads() per
# request would add avoidable I/O latency to the live path. The file only
# changes when scripts/calibrate.py reruns (weekly/monthly), so an mtime check
# is enough to pick up updates without re-reading on every call.
_cache_path: Path | None = None
_cache_mtime: float | None = None
_cache_rates: dict[str, float] | None = None


def load_strength_hit_rates(path: str = CALIBRATION_FILE) -> dict[str, float] | None:
    """Load a previously-saved strength hit-rate table, if one exists.

    Cached in-process by (path, mtime) — cheap to call on every request.

    Args:
        path: Source file path.

    Returns:
        The rate map, or None if the file doesn't exist / is unreadable — callers
        should treat None exactly like "no calibration data yet" (ConfluenceRanker's
        default, uncalibrated behavior).
    """
    global _cache_path, _cache_mtime, _cache_rates

    src = Path(path)
    if not src.exists():
        return None

    try:
        mtime = src.stat().st_mtime
    except OSError as exc:
        logger.warning("calibration: failed to stat %s: %s — ignoring", src, exc)
        return None

    if _cache_path == src and _cache_mtime == mtime:
        return _cache_rates

    try:
        data = json.loads(src.read_text())
        if not isinstance(data, dict):
            raise ValueError("calibration file must contain a JSON object")
        rates = {str(k): float(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("calibration: failed to load %s: %s — ignoring", src, exc)
        return None

    _cache_path, _cache_mtime, _cache_rates = src, mtime, rates
    return rates


def load_strength_hit_rates_from_supabase(
    horizon_days: int | None = None,
) -> dict[str, float] | None:
    """Load the active strength-bucket calibration generation from Supabase.

    This is what scripts/scan_universe.py calls in production — see the
    module docstring for why this differs from load_strength_hit_rates().

    Args:
        horizon_days: Filter to one horizon. None returns the active
            generation regardless of horizon (there is normally only one
            active horizon at a time, since scripts/calibrate_supabase.py
            is invoked with a single --horizon-days per run).

    Returns:
        The rate map, or None if Supabase isn't configured, the request
        fails, or no active generation exists yet — callers should treat
        None exactly like "no calibration data yet" (ConfluenceRanker's
        default, uncalibrated behavior). Never raises.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return None

    params: dict[str, str] = {
        "select": "bucket_key,hit_rate",
        "bucket_kind": "eq.strength",
        "is_active": "eq.true",
    }
    if horizon_days is not None:
        params["horizon_days"] = f"eq.{horizon_days}"

    try:
        resp = httpx.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/calibration",
            params=params,
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            },
            timeout=SUPABASE_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        rows = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("calibration: failed to load from Supabase: %s — ignoring", exc)
        return None

    if not rows:
        return None
    return {r["bucket_key"]: float(r["hit_rate"]) for r in rows}
