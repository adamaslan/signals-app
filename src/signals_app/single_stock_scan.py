"""Actual offline single-stock broad scan. No provider, model, DB or deployment.

Unlike historical_replay's directional outcome buckets, retain every detector
observation, including neutral/context, and separate repeated states from state
transitions. Nested windows overlap and must not be summed as independent hits.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import pandas as pd

from signals_app.config import SIGNALS_APP_CODE_VERSION
from signals_app.detection.orchestrator import get_default_detectors
from signals_app.indicators.compute import compute_indicators

RUNNER_VERSION = 'single-stock-broadscan@1.0.0'
WINDOWS = (21, 63, 126, 172)


def read_local_bars(path: Path, end: str) -> pd.DataFrame:
    """Strict local daily CSV, oldest first, no repair, fetch or hidden fallback."""
    frame = pd.read_csv(path)
    required = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
    if not set(required) <= set(frame):
        raise ValueError('CSV needs Date,Open,High,Low,Close,Volume')
    for value in frame['Date']:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('Daily dates must be real YYYY-MM-DD values without timezone ambiguity')
    frame['Date'] = pd.to_datetime(frame['Date'], errors='raise')
    if frame['Date'].duplicated().any() or not frame['Date'].is_monotonic_increasing:
        raise ValueError('Dates require unique ascending rows')
    frame = frame.set_index('Date').loc[:end, required[1:]]
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors='raise')
    if frame.empty or not frame.map(lambda x: isinstance(x, (int, float)) and math.isfinite(x)).all().all():
        raise ValueError('Empty or nonfinite OHLCV')
    if (frame[['Open', 'High', 'Low', 'Close']] <= 0).any().any() or (frame['Volume'] < 0).any():
        raise ValueError('Nonpositive prices or negative volume')
    if ((frame['High'] < frame[['Open', 'Close', 'Low']].max(axis=1)) | (frame['Low'] > frame[['Open', 'Close', 'High']].min(axis=1))).any():
        raise ValueError('Inconsistent OHLC')
    return frame


def scan_single_stock(frame: pd.DataFrame, *, symbol: str, start: str, end: str,
                      source: str, price_basis: str, warmup: int = 252,
                      detectors=None, today: date | None = None) -> dict:
    """Execute real detectors on recomputed prefixes. No artificial signal quota."""
    if not re.fullmatch(r'[A-Z][A-Z0-9.^-]{0,19}', symbol):
        raise ValueError('One explicit stock symbol required')
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    if start_day > end_day or end_day > (today or date.today()):
        raise ValueError('Invalid or future completed-session range')
    if not source.strip() or price_basis != 'split-adjusted-ohlc':
        raise ValueError('Declare a local source and consistent split-adjusted OHLC; unknown/raw basis is not verified')
    if not isinstance(warmup, int) or warmup < 252:
        raise ValueError('At least252 prior bars required for broad scan warmup')
    frame = frame.loc[:end].copy()
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is not None or frame.index.hasnans:
        raise ValueError('Timezone-naive daily index required')
    if not frame.index.equals(frame.index.normalize()):
        raise ValueError('Expected completed daily bars, not intraday timestamps')
    if frame.index.duplicated().any() or not frame.index.is_monotonic_increasing:
        raise ValueError('Unique ascending dates required')
    needed = ['Open', 'High', 'Low', 'Close', 'Volume']
    if not set(needed) <= set(frame) or frame.empty:
        raise ValueError('No usable OHLCV')
    values = frame[needed]
    if not values.map(lambda x: isinstance(x, (int, float)) and math.isfinite(x)).all().all():
        raise ValueError('Nonfinite or nonnumeric OHLCV')
    if (values[['Open', 'High', 'Low', 'Close']] <= 0).any().any() or (values.Volume < 0).any():
        raise ValueError('Invalid price or volume')
    if ((values.High < values[['Open', 'Close', 'Low']].max(axis=1)) | (values.Low > values[['Open', 'Close', 'High']].min(axis=1))).any():
        raise ValueError('Inconsistent OHLC')
    if ((frame.index.to_series().diff().dt.days.dropna() > 7).any()
            or (end_day - frame.index[-1].date()).days > 7):
        raise ValueError('Stale or gapped daily input; seven-day guard is not exchange-calendar certification')
    positions = [i for i, stamp in enumerate(frame.index) if start <= stamp.date().isoformat() <= end]
    if not positions or positions[0] < warmup:
        raise ValueError('No evaluation rows or insufficient prior252-bar warmup')
    if len(positions) > 366:
        raise ValueError('Bounded local run: at most366 evaluation bars')
    selected = list(get_default_detectors() if detectors is None else detectors)
    names = [type(d).__name__ for d in selected]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate detector identity would inflate scan counts')
    observations, coverage, errors = [], [], []
    previous = set()
    previous_succeeded = set()
    for pos in positions:
        stamp = frame.index[pos].date().isoformat()
        prefix = compute_indicators(frame.iloc[:pos + 1].copy())
        current = set()
        succeeded = set()
        for detector in selected:
            name = type(detector).__name__
            try:
                emitted = detector.detect(prefix)
                seen = set()
                pending = []
                for signal in emitted:
                    key = (name, signal.signal, signal.category, signal.strength)
                    if key in seen:
                        continue
                    seen.add(key)
                    pending.append({'date': stamp, 'detector': name, 'signal': signal.signal,
                                         'category': signal.category, 'strength': signal.strength,
                                         'description': signal.description,
                                         'new_state': name in previous_succeeded and key not in previous})
                # Publish only after the entire detector result validates.
                observations.extend(pending)
                current.update(seen)
                succeeded.add(name)
                coverage.append({'date': stamp, 'detector': name, 'status': 'ok', 'observations': len(seen)})
            except Exception as exc:
                errors.append({'date': stamp, 'detector': name, 'error': type(exc).__name__})
                coverage.append({'date': stamp, 'detector': name, 'status': 'error', 'observations': 0})
        # Only compare adjacent successfully observed states. First row marks
        # initialization, not proof that a market event happened on that day.
        previous = current
        previous_succeeded = succeeded
    for observation in observations:
        if observation['date'] == frame.index[positions[0]].date().isoformat():
            observation['new_state'] = False
    by_window = {}
    for window in WINDOWS:
        dates = {frame.index[p].date().isoformat() for p in positions[-window:]}
        rows = [o for o in observations if o['date'] in dates]
        by_window[str(window)] = {'bars_observed': min(window, len(positions)),
            'observations': len(rows), 'new_states': sum(o['new_state'] for o in rows),
            'by_detector': dict(Counter(o['detector'] for o in rows))}
    canonical = frame[needed].to_csv(date_format='%Y-%m-%d', float_format='%.17g')
    return {'runner_version': RUNNER_VERSION, 'engine_version': SIGNALS_APP_CODE_VERSION,
        'symbol': symbol, 'start': start, 'end': end, 'source_declared': source,
        'price_basis_declared': price_basis, 'basis_independently_verified': False,
        'input_sha256': hashlib.sha256(canonical.encode()).hexdigest(),
        'input_bars': len(frame), 'warmup_available': positions[0],
        'evaluation_bars': len(positions), 'detector_count': len(selected), 'detectors': names,
        'detector_evaluations': len(coverage), 'failed_evaluations': len(errors),
        'status': 'degraded' if errors else 'completed', 'observations': observations,
        'new_state_count': sum(o['new_state'] for o in observations), 'coverage': coverage,
        'errors': errors, 'nested_windows': by_window,
        'limitations': ['Research reconstruction, not live trading or a measured edge',
            'Nested windows overlap; observations are not independent signals or raw fetched rows',
            'New state means first observed after adjacent absence, not an authenticated corporate event',
            '19 existing detector classes, not500 newly activated detectors',
            '100-source18M extension not executed; unavailable feeds are not invented',
            'No provider, model, DB, deployment or promotion in this runner']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--price-basis', required=True, choices=['split-adjusted-ohlc', 'unknown', 'raw'])
    args = parser.parse_args(argv)
    try:
        frame = read_local_bars(args.csv, args.end)
        result = scan_single_stock(frame, symbol=args.symbol, start=args.start, end=args.end,
                                   source=args.source, price_basis=args.price_basis)
        print(json.dumps(result, allow_nan=False))
        return 2 if result['errors'] else 0
    except (ValueError, OSError, TypeError) as exc:
        print(f'Scan refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
