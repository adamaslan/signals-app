"""Offline explicit-window signal replay. Never fetches, writes a DB, or calls a model.

Input: one Date,Open,High,Low,Close,Volume CSV per seed ticker and metadata.json
with source, adjustment, retrieved_at, calendar_policy. Output is research only.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
import pandas as pd


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_bars(path: Path, cutoff: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if not {"Date", "Open", "High", "Low", "Close", "Volume"} <= set(frame):
        raise ValueError("missing OHLCV columns")
    frame["Date"] = pd.to_datetime(frame["Date"], errors="raise").dt.normalize()
    if frame["Date"].duplicated().any():
        raise ValueError("duplicate dates")
    if not frame["Date"].is_monotonic_increasing:
        raise ValueError("dates not increasing")
    frame = frame.set_index("Date").loc[:cutoff]
    values = frame[["Open", "High", "Low", "Close", "Volume"]]
    if not values.map(lambda x: isinstance(x, (int, float)) and math.isfinite(x)).all().all():
        raise ValueError("invalid/non-finite OHLCV")
    if (values[["Open", "High", "Low", "Close"]] <= 0).any().any() or (values.Volume < 0).any():
        raise ValueError("nonpositive prices or negative volume")
    if ((values.High < values[["Open", "Close", "Low"]].max(axis=1)) | (values.Low > values[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError("inconsistent OHLC")
    return frame


def replay(frame: pd.DataFrame, start: str, end: str, horizons: list[int], warmup: int = 200) -> dict:
    """Recompute every decision from its prefix, including indicators. No future input.

    Outcomes use only rows through end. Horizons are observed bars, not claims
    of exact exchange-session coverage. Gaps are surfaced by the input ledger.
    """
    from signals_app.indicators.compute import compute_indicators
    from signals_app.detection.orchestrator import get_default_detectors
    frame = frame.loc[:end]
    decisions = []
    buckets: dict[tuple, dict] = {}
    eligible = frame.index[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]
    if len(eligible) == 0:
        return {"status": "no_window_bars", "decisions": [], "buckets": []}
    first = frame.index.get_loc(eligible[0])
    if first < warmup:
        return {"status": "insufficient_warmup", "warmup_available": first, "decisions": [], "buckets": []}
    detectors = get_default_detectors()
    failures = 0
    for stamp in eligible:
        pos = frame.index.get_loc(stamp)
        prefix = compute_indicators(frame.iloc[:pos + 1].copy())
        signals = []
        errors = []
        for detector in detectors:
            try:
                signals.extend(detector.detect(prefix))
            except Exception as exc:
                errors.append({"detector": type(detector).__name__, "error": type(exc).__name__})
        failures += len(errors)
        calls = []
        for sig in signals:
            direction = 1 if "BULLISH" in sig.strength else -1 if "BEARISH" in sig.strength else 0
            if not direction:
                continue
            calls.append({"detector": sig.signal, "category": sig.category, "strength": sig.strength})
            for h in horizons:
                key = (sig.signal, sig.category, sig.strength, h)
                b = buckets.setdefault(key, {"detector": sig.signal, "category": sig.category, "strength": sig.strength, "horizon_bars": h, "hits": 0, "misses": 0, "flat": 0, "pending": 0, "directional_return_sum": 0.0})
                if pos + h >= len(frame):
                    b["pending"] += 1
                else:
                    ret = (float(frame.Close.iloc[pos + h]) / float(frame.Close.iloc[pos]) - 1) * direction
                    b["hits" if ret > 0 else "misses" if ret < 0 else "flat"] += 1
                    b["directional_return_sum"] += ret
        decisions.append({"date": stamp.date().isoformat(), "calls": calls, "detector_errors": errors})
    for b in buckets.values():
        n = b["hits"] + b["misses"] + b["flat"]
        b["resolved_directional"] = n
        b["hit_rate"] = b["hits"] / n if n >= 30 else None
        b["mean_directional_price_return"] = b["directional_return_sum"] / n if n else None
    return {"status": "degraded" if failures else "ok", "warmup_available": first, "decision_bars": len(eligible), "first_decision": eligible[0].date().isoformat(), "last_decision": eligible[-1].date().isoformat(), "decisions": decisions, "buckets": list(buckets.values())}


def run(seed: Path, bars_dir: Path, output: Path, start: str, end: str, horizons: list[int]) -> dict:
    if date.fromisoformat(start) > date.fromisoformat(end) or not horizons or any(h <= 0 for h in horizons):
        raise ValueError("invalid window/horizons")
    metadata = json.loads((bars_dir / "metadata.json").read_text())
    for key in ("source", "adjustment", "retrieved_at", "calendar_policy"):
        if not metadata.get(key):
            raise ValueError("missing dataset provenance: " + key)
    rows = list(csv.DictReader(seed.open()))
    symbols = [r["ticker"].strip().upper() for r in rows]
    if len(set(symbols)) != len(symbols) or any(not s or "/" in s or "\\" in s or s in (".", "..") for s in symbols):
        raise ValueError("invalid or duplicate seed ticker")
    output.mkdir(parents=True, exist_ok=True)
    ledger = []
    pooled = {}
    assets = {r["ticker"].strip().upper(): r.get("asset_type", "Unknown") for r in rows}
    for symbol in symbols:
        path = bars_dir / (symbol + ".csv")
        record = {"ticker": symbol, "status": "missing_history"}
        if path.exists():
            record["input_sha256"] = sha(path)
            try:
                frame = read_bars(path, end)
                record.update(replay(frame, start, end, horizons))
                # US exchange calendar is a declared reference, not inferred listing venue.
                import exchange_calendars as calendars
                if assets[symbol] == "Crypto":
                    expected = pd.date_range(start, end, freq="D")
                    calendar = "24/7 UTC dates"
                else:
                    expected = calendars.get_calendar("XNYS").sessions_in_range(start, end).tz_localize(None)
                    calendar = "XNYS reference; actual listing venue unverified"
                missing = expected.difference(frame.index)
                extra = frame.loc[start:end].index.difference(expected)
                record.update(asset_type=assets[symbol], calendar=calendar, expected_decision_bars=len(expected), missing_decision_dates=[d.date().isoformat() for d in missing], unexpected_decision_dates=[d.date().isoformat() for d in extra])
                if len(missing) or len(extra):
                    record["status"] = "partial_calendar_coverage"
                for bucket in record.get("buckets", []) if record["status"] == "ok" else []:
                    key = (bucket["category"], bucket["strength"], bucket["horizon_bars"])
                    total = pooled.setdefault(key, {"category": key[0], "strength": key[1], "horizon_bars": key[2], "hits": 0, "misses": 0, "flat": 0, "pending": 0, "directional_return_sum": 0.0})
                    for field in ("hits", "misses", "flat", "pending", "directional_return_sum"):
                        total[field] += bucket[field]
                record["observed_dates"] = [d.date().isoformat() for d in frame.index]
            except Exception as exc:
                record.update(status="error", error_type=type(exc).__name__, error=str(exc))
        (output / (symbol + ".json")).write_text(json.dumps(record, sort_keys=True))
        ledger.append({k: v for k, v in record.items() if k not in ("decisions", "buckets", "observed_dates")})
    for bucket in pooled.values():
        n = bucket["hits"] + bucket["misses"] + bucket["flat"]
        bucket["resolved_directional"] = n
        bucket["hit_rate"] = bucket["hits"] / n if n >= 30 else None
        bucket["mean_directional_price_return"] = bucket["directional_return_sum"] / n if n else None
    source_dir = Path(__file__).parent
    engine_files = sorted([source_dir / "config.py", Path(__file__), *source_dir.glob("indicators/*.py"), *source_dir.glob("detection/*.py")])
    engine_hash = hashlib.sha256("\n".join(str(p.relative_to(source_dir)) + ":" + sha(p) for p in engine_files).encode()).hexdigest()
    manifest = {"engine_source_sha256": engine_hash, "pooled_category_strength_horizon": list(pooled.values()), "aggregate_eligibility": "only status=ok, incomplete calendars excluded; per-symbol partial details research-only", "denominator_policy": "signal occurrences, not independent trades; flat counts as non-hit; pooled counts before division, n>=30 display gate", "timing": "signal computed after decision close; close-to-close evaluation, not executable fill at same close", "mode": "reconstructed_point_in_time", "label": "Historical backtest reconstruction, not a live track record", "computed_at": datetime.now(timezone.utc).isoformat(), "decision_start": start, "observation_cutoff": end, "seed_sha256": sha(seed), "requested_tickers": len(symbols), "source": metadata, "horizons_observed_bars": horizons, "warmup_bars": 200, "universe_limitation": "current membership, survivorship-limited", "price_return_only": True, "fees_slippage_borrow": "not modeled; not tradable P&L", "ledger": ledger}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=Path, required=True)
    p.add_argument("--bars-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizons", default="1,5,20,21,63,126,252")
    a = p.parse_args()
    manifest = run(a.seed, a.bars_dir, a.output, a.start, a.end, [int(h) for h in a.horizons.split(",")])
    print(json.dumps({"requested": manifest["requested_tickers"], "status_counts": {s: sum(r["status"] == s for r in manifest["ledger"]) for s in sorted({r["status"] for r in manifest["ledger"]})}}))

if __name__ == "__main__":
    main()
