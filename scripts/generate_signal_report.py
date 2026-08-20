#!/usr/bin/env python3
"""Generate a human-readable Markdown signal report for one or more tickers.

Usage:
    python scripts/generate_signal_report.py AAPL
    python scripts/generate_signal_report.py --seed seed/universe_symbols.csv --limit 5
    python scripts/generate_signal_report.py AAPL MSFT --out-dir docs/reports

Mirrors the "At a Glance" / "Signals by Strength" / "Signals by Category"
report shape in homebase/docs/boll4-500b.md, generated from signals-app's own
pipeline instead of hand-written. Reuses the same L1-L4 layers
scripts/scan_universe.py calls (fetch -> indicators -> detect -> confluence)
rather than duplicating pipeline logic.

No LLM calls, no Supabase writes — a local analysis/documentation tool, not
a production path.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from signals_app.config import DEFAULT_PERIOD  # noqa: E402
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.detection.orchestrator import detect_all_signals  # noqa: E402
from signals_app.indicators.compute import compute_indicators  # noqa: E402
from signals_app.indicators.data_quality import score_data_quality  # noqa: E402
from signals_app.scoring.confluence import ConfluenceRanker  # noqa: E402
from scripts.scan_universe import load_symbols_from_csv  # noqa: E402

TOP_N_SIGNALS = 15
_SAFE_TICKER_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_filename_component(ticker: str) -> str:
    """Collapse a ticker into a single safe filename component.

    Strips path separators, `..`, and any other character outside
    [A-Za-z0-9_.-] so a ticker sourced from CLI args or a seed CSV can never
    make out_path resolve outside out_dir.
    """
    return _SAFE_TICKER_RE.sub("_", ticker).strip("._") or "UNKNOWN"


def _render_report(
    ticker: str,
    period: str,
    generated_at: str,
    bar_count: int,
    data_quality_score: float | None,
    signals: list[Any],
    degraded: bool,
    confluence: Any,
) -> str:
    """Build the Markdown report body for one ticker's scan result."""
    strength_counts = Counter(s.strength for s in signals)
    category_counts = Counter(s.category for s in signals)
    signal_name_counts = Counter(s.signal for s in signals)

    data_quality_text = (
        f"{data_quality_score:.2f}" if data_quality_score is not None else "N/A"
    )

    lines: list[str] = [
        f"# Signal Report — {ticker}",
        "",
        f"**Generated:** {generated_at} · **Period:** {period} · "
        f"**Bars:** {bar_count} · **Data quality:** {data_quality_text}",
        "",
        "---",
        "",
        "## At a Glance",
        "",
        "| Stat | Value |",
        "|------|-------|",
        f"| Total signals | {len(signals)} |",
        f"| Unique signal names | {len(signal_name_counts)} |",
        f"| Confluence score | {confluence.score:.3f} |",
        f"| Bias | {confluence.bias} |",
        f"| Action | {confluence.action} |",
        f"| Bull / Bear count | {confluence.bull_count} / {confluence.bear_count} |",
        f"| Detector degraded | {degraded} |",
        "",
        "---",
        "",
        "## Signals by Strength",
        "",
        "| Strength | Count |",
        "|----------|-------|",
    ]
    for strength, count in strength_counts.most_common():
        lines.append(f"| {strength} | {count} |")

    lines += [
        "",
        "## Signals by Category",
        "",
        "| Category | Count | Share |",
        "|----------|-------|-------|",
    ]
    total = len(signals) or 1
    for category, count in category_counts.most_common():
        lines.append(f"| {category} | {count} | {count / total:.0%} |")

    lines += [
        "",
        f"## Top {TOP_N_SIGNALS} Most Active Signals",
        "",
        "| Signal | Count |",
        "|--------|-------|",
    ]
    for name, count in signal_name_counts.most_common(TOP_N_SIGNALS):
        lines.append(f"| {name} | {count} |")

    lines += [
        "",
        "---",
        "",
        "## All Signals (latest bar)",
        "",
        "| Signal | Strength | Category | Description |",
        "|--------|----------|----------|--------------|",
    ]
    for s in signals:
        lines.append(f"| {s.signal} | {s.strength} | {s.category} | {s.description} |")

    lines.append("")
    return "\n".join(lines)


def generate_report_for_symbol(
    ticker: str, period: str, out_dir: Path
) -> Path | None:
    """Run L1-L4 for one ticker and write its Markdown report.

    Returns:
        Path to the written report, or None if the ticker had insufficient
        data and was skipped.
    """
    fetcher = DataFetcher()
    ohlcv = fetcher.fetch(ticker, period)
    if len(ohlcv.df) < 20:
        print(f"  {ticker}: skipped — insufficient bars ({len(ohlcv.df)})")
        return None

    data_quality = score_data_quality(ohlcv.df, period)
    df = compute_indicators(ohlcv.df)
    signal_list = detect_all_signals(df)

    ranker = ConfluenceRanker()
    confluence = ranker.rank_signals(list(signal_list))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = _render_report(
        ticker=ticker,
        period=period,
        generated_at=generated_at,
        bar_count=len(df),
        data_quality_score=data_quality.score,
        signals=list(signal_list),
        degraded=signal_list.degraded,
        confluence=confluence,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_ticker = _safe_filename_component(ticker)
    out_path = out_dir / f"{safe_ticker}_{ts}_optimal.md"
    out_path.write_text(report)
    print(f"  {ticker}: {len(signal_list)} signals -> {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("symbols", nargs="*", help="Ticker symbols to report on")
    parser.add_argument(
        "--seed", help="CSV file with a 'ticker' column (e.g. seed/universe_symbols.csv)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of symbols reported (must be positive)",
    )
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument(
        "--out-dir",
        default="docs/reports",
        help="Directory to write {ticker}_{ts}_optimal.md files into",
    )
    args = parser.parse_args()

    symbols = list(args.symbols)
    if args.seed:
        symbols.extend(load_symbols_from_csv(args.seed))
    if not symbols:
        parser.error("no symbols given — pass tickers directly or use --seed")
    symbols = sorted(set(symbols))
    if args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit must be a positive integer")
        symbols = symbols[: args.limit]

    out_dir = _project_root / args.out_dir
    print(f"Generating reports for {len(symbols)} symbol(s) -> {out_dir}")
    written: list[Path] = []
    for t in symbols:
        try:
            p = generate_report_for_symbol(t, args.period, out_dir)
        except Exception as exc:
            print(f"  {t}: failed — {exc}")
            continue
        if p is not None:
            written.append(p)
    print(f"Done — {len(written)}/{len(symbols)} reports written")


if __name__ == "__main__":
    main()
