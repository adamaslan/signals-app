#!/usr/bin/env python3
"""CLI analysis script.

Usage:
    python scripts/analyze.py AAPL
    python scripts/analyze.py AAPL --timeframe 1d
    python scripts/analyze.py AAPL --timeframe 1d --no-llm
    python scripts/analyze.py AAPL --period 6mo --no-llm --output ./output/aapl.json

Runs through L1-L5 of the pipeline and prints a JSON result.
--no-llm skips synthesis and returns the confluence score only.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add src/ to path when running as a script
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))

from signals_app.config import DEFAULT_PERIOD, VALID_PERIODS, get_settings
from signals_app.data.fetcher import DataFetcher
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker
from signals_app.utils.safety import safe_json_dumps

logger = logging.getLogger(__name__)


def _period_to_timeframe(period: str) -> str:
    """Map a yfinance period string to a Timeframe label."""
    mapping = {
        "1d": "1D", "5d": "5D", "1mo": "1M", "3mo": "3M", "6mo": "6M", "1y": "1Y",
    }
    return mapping.get(period, "1D")


def run_analysis(
    symbol: str,
    period: str,
    no_llm: bool,
    output_path: str | None,
) -> dict:
    """Run the full or partial pipeline for a symbol.

    Args:
        symbol: Stock ticker symbol.
        period: yfinance period string.
        no_llm: If True, skip LLM synthesis.
        output_path: Optional path to write JSON output.

    Returns:
        Result dictionary.
    """
    settings = get_settings()
    symbol = symbol.upper().strip()

    logger.info("analyze: %s period=%s no_llm=%s", symbol, period, no_llm)

    # L1: Fetch
    fetcher = DataFetcher(settings=settings)
    ohlcv = fetcher.fetch(symbol, period)
    logger.info("Fetched %d bars for %s", ohlcv.bar_count, symbol)

    # L2: Indicators
    df = compute_indicators(ohlcv.df)
    logger.info("Computed %d indicator columns", len(df.columns))

    # L3: Detection
    signal_list = detect_all_signals(df)
    logger.info(
        "Detected %d signals (degraded=%s warnings=%d)",
        len(signal_list), signal_list.degraded, len(signal_list.warnings),
    )

    # L4: Confluence
    ranker = ConfluenceRanker()
    confluence = ranker.rank_signals(list(signal_list))
    logger.info(
        "Confluence: score=%.3f action=%s bull=%d bear=%d",
        confluence.score, confluence.action, confluence.bull_count, confluence.bear_count,
    )

    result: dict = {
        "symbol": symbol,
        "period": period,
        "bar_count": ohlcv.bar_count,
        "from_cache": ohlcv.from_cache,
        "detection": {
            "total_signals": len(signal_list),
            "degraded": signal_list.degraded,
            "warnings": signal_list.warnings,
        },
        "confluence": confluence.to_dict(),
    }

    # L5: LLM synthesis (optional)
    if not no_llm:
        timeframe = _period_to_timeframe(period)
        features = {
            "symbol": symbol,
            "period": period,
            "confluence_score": confluence.score,
            "bias": confluence.bias,
            "action": confluence.action,
            "bull_count": confluence.bull_count,
            "bear_count": confluence.bear_count,
            "total_signals": len(signal_list),
        }

        # Add key indicator values
        if len(df) > 0:
            current = df.iloc[-1]
            for col in ["RSI", "MACD", "ADX", "Close", "ATR", "Price_Change"]:
                try:
                    import math
                    v = float(current[col])
                    if not (math.isnan(v) or math.isinf(v)):
                        features[col.lower()] = round(v, 4)
                except Exception:
                    pass

        from signals_app.synthesis.mtf_llm import synthesize_single
        signal = synthesize_single(
            ticker=symbol,
            timeframe=timeframe,
            features=features,
            settings=settings,
        )
        result["signal"] = signal.model_dump()
        logger.info(
            "Signal: direction=%s confidence=%.3f ai_degraded=%s",
            signal.direction.value, signal.confidence, signal.ai_degraded,
        )
    else:
        result["signal"] = None
        logger.info("LLM synthesis skipped (--no-llm)")

    # Write output file in local mode
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(safe_json_dumps(result, indent=2))
        logger.info("Result written to %s", out)
    elif settings.env == "local":
        out_dir = Path(settings.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{symbol}_{period}.json"
        out_file.write_text(safe_json_dumps(result, indent=2))
        logger.info("Result written to %s", out_file)

    return result


def main() -> None:
    """Parse arguments and run analysis."""
    parser = argparse.ArgumentParser(
        description="Signals App CLI — run the signal pipeline for a ticker"
    )
    parser.add_argument("symbol", help="Ticker symbol (e.g., AAPL)")
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        choices=list(VALID_PERIODS),
        help=f"yfinance period (default: {DEFAULT_PERIOD})",
    )
    parser.add_argument(
        "--timeframe",
        default=None,
        help="Deprecated — use --period instead",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        default=False,
        help="Skip LLM synthesis, return confluence score only",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output file path (default: ./output/{SYMBOL}_{period}.json)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    period = args.period

    try:
        result = run_analysis(
            symbol=args.symbol,
            period=period,
            no_llm=args.no_llm,
            output_path=args.output,
        )
        print(safe_json_dumps(result, indent=2))
    except Exception as exc:
        logger.error("Analysis failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
