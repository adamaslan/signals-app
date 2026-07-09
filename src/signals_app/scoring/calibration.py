"""Confidence calibration — loads/derives the strength -> hit-rate table.

ConfluenceRanker.rank_signals() already accepts an optional strength_hit_rates
map and nudges confidence_label toward measured backtest hit-rates when one is
supplied (see scoring/confluence.py). This module is the missing other half:
computing that table from backtests.engine.score_historical_signals() output
and persisting/loading it so the live API path can pass it in.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from signals_app.config import CALIBRATION_FILE, CALIBRATION_MIN_BUCKET_SIZE

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


def load_strength_hit_rates(path: str = CALIBRATION_FILE) -> dict[str, float] | None:
    """Load a previously-saved strength hit-rate table, if one exists.

    Args:
        path: Source file path.

    Returns:
        The rate map, or None if the file doesn't exist / is unreadable — callers
        should treat None exactly like "no calibration data yet" (ConfluenceRanker's
        default, uncalibrated behavior).
    """
    src = Path(path)
    if not src.exists():
        return None
    try:
        data = json.loads(src.read_text())
        if not isinstance(data, dict):
            raise ValueError("calibration file must contain a JSON object")
        return {str(k): float(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("calibration: failed to load %s: %s — ignoring", src, exc)
        return None
