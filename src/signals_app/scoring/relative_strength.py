"""Relative strength scoring — ranks a symbol vs a universe of peers.

Adapted from gcp3/backend/technical_signals.py.
Computes multi-period return-based signals: momentum, trend, pullback,
52-week range position, and universe ranking.

In the new app this is decoupled from Firestore — it accepts pre-fetched
return dictionaries so the caller controls the data source.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

# Action ordering for sorting
_ACTION_ORDER: Final[dict[str, int]] = {"BUY": 0, "HOLD": 1, "SELL": 2}

# Confluence score thresholds
_BUY_SCORE_THRESHOLD: Final[float] = 0.35
_SELL_SCORE_THRESHOLD: Final[float] = -0.35
_BUY_MIN_SIGNALS: Final[int] = 3
_SELL_MIN_SIGNALS: Final[int] = 3


@dataclass
class RelativeStrengthResult:
    """Result of relative strength analysis for a single symbol.

    Attributes:
        symbol: Ticker or ETF symbol.
        action: "BUY", "HOLD", or "SELL".
        confluence_score: Weighted net score in [-1, 1].
        confidence_label: "HIGH", "MEDIUM", or "LOW".
        bull_count: Number of bullish signals.
        bear_count: Number of bearish signals.
        rank_in_universe: 1-based rank (1 = strongest) in peer universe.
        universe_size: Total symbols in the comparison universe.
        signals: List of raw signal dicts with detail and weight.
        returns: Input return data for auditability.
    """

    symbol: str
    action: str
    confluence_score: float
    confidence_label: str
    bull_count: int
    bear_count: int
    rank_in_universe: int
    universe_size: int
    signals: list[dict[str, Any]]
    returns: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary.

        Returns:
            Plain dictionary representation.
        """
        return {
            "symbol": self.symbol,
            "action": self.action,
            "confluence_score": self.confluence_score,
            "confidence_label": self.confidence_label,
            "bull_count": self.bull_count,
            "bear_count": self.bear_count,
            "rank_in_universe": self.rank_in_universe,
            "universe_size": self.universe_size,
            "signals": self.signals,
            "returns": self.returns,
        }


def _add_signal(
    signals: list[dict],
    weighted_bull: list[float],
    weighted_bear: list[float],
    max_weight: list[float],
    signal: str,
    detail: str,
    strength: str,
    value: float,
    weight: float,
    category: str,
) -> None:
    """Append a signal and update the running weighted vote totals.

    Args:
        signals: Mutable list to append to.
        weighted_bull: Single-element list holding running bull weight.
        weighted_bear: Single-element list holding running bear weight.
        max_weight: Single-element list holding running max weight.
        signal: Short label.
        detail: One-sentence explanation.
        strength: "BULLISH", "BEARISH", or "NEUTRAL".
        value: Raw numeric value driving the signal.
        weight: Contribution weight (1–3).
        category: Signal category string.
    """
    signals.append({
        "signal": signal,
        "detail": detail,
        "strength": strength,
        "value": value,
        "weight": weight,
        "category": category,
    })
    max_weight[0] += weight
    if strength == "BULLISH":
        weighted_bull[0] += weight
    elif strength == "BEARISH":
        weighted_bear[0] += weight


def score_symbol(
    symbol: str,
    returns: dict[str, float | None],
    rank_1d: int,
    universe_size: int,
    name: str | None = None,
) -> RelativeStrengthResult:
    """Derive BUY/HOLD/SELL + signals from multi-period return data.

    Args:
        symbol: Ticker or ETF symbol.
        returns: Dict with keys "1d", "1w", "1m", "3m", "6m", "1y".
            Values are percentage returns or None if unavailable.
        rank_1d: 1-based rank of this symbol by today's return in the universe.
        universe_size: Total number of symbols in the comparison universe.
        name: Human-readable name for the symbol (default: symbol).

    Returns:
        RelativeStrengthResult with action, score, and detailed signals.
    """
    display_name = name or symbol
    r1d = returns.get("1d")
    r1w = returns.get("1w")
    r1m = returns.get("1m")
    r3m = returns.get("3m")
    r6m = returns.get("6m")
    r1y = returns.get("1y")

    signals: list[dict] = []
    weighted_bull = [0.0]
    weighted_bear = [0.0]
    max_weight = [0.0]

    def _add(signal: str, detail: str, strength: str, value: float, weight: float, category: str) -> None:
        _add_signal(signals, weighted_bull, weighted_bear, max_weight,
                    signal, detail, strength, value, weight, category)

    # Signal 1: Short-term momentum (1d + 1w)
    if r1d is not None and r1w is not None:
        if r1d > 0 and r1w > 0:
            _add(
                f"Short-term momentum bullish (1d {r1d:+.2f}%, 1w {r1w:+.1f}%)",
                f"{display_name} is up both today and this week, suggesting near-term buying pressure.",
                "BULLISH", r1d, 1, "momentum",
            )
        elif r1d < 0 and r1w < 0:
            _add(
                f"Short-term momentum bearish (1d {r1d:+.2f}%, 1w {r1w:+.1f}%)",
                f"{display_name} is down both today and this week, indicating near-term selling pressure.",
                "BEARISH", r1d, 1, "momentum",
            )
        else:
            _add(
                f"Short-term momentum mixed (1d {r1d:+.2f}%, 1w {r1w:+.1f}%)",
                "Today's move conflicts with the weekly direction — neither side has sustained control.",
                "NEUTRAL", r1d, 1, "momentum",
            )

    # Signal 2: Medium-term momentum (1m + 3m)
    if r1m is not None and r3m is not None:
        if r1m > 0 and r3m > 2:
            _add(
                f"Medium-term momentum bullish (1m {r1m:+.1f}%, 3m {r3m:+.1f}%)",
                "Both 1-month and 3-month returns are positive, confirming multi-week conviction.",
                "BULLISH", r1m, 2, "momentum",
            )
        elif r1m < 0 and r3m < -2:
            _add(
                f"Medium-term momentum bearish (1m {r1m:+.1f}%, 3m {r3m:+.1f}%)",
                "Both 1-month and 3-month returns are negative, signaling sustained weakness.",
                "BEARISH", r1m, 2, "momentum",
            )
        else:
            _add(
                f"Medium-term momentum neutral (1m {r1m:+.1f}%, 3m {r3m:+.1f}%)",
                "1-month and 3-month returns diverge — likely consolidation or rotation.",
                "NEUTRAL", r1m, 2, "momentum",
            )

    # Signal 3: Long-term trend (1y)
    if r1y is not None:
        if r1y > 15:
            _add(
                f"Strong long-term uptrend ({r1y:+.1f}% 1y)",
                f"A {r1y:+.1f}% annual return indicates structural tailwinds persisting over a year.",
                "BULLISH", r1y, 2, "trend",
            )
        elif r1y > 0:
            _add(
                f"Moderate long-term uptrend ({r1y:+.1f}% 1y)",
                f"{display_name} gained over the past year at a steady pace.",
                "BULLISH", r1y, 1, "trend",
            )
        elif r1y < -15:
            _add(
                f"Strong long-term downtrend ({r1y:+.1f}% 1y)",
                f"A {r1y:+.1f}% annual loss indicates deep structural weakness.",
                "BEARISH", r1y, 2, "trend",
            )
        elif r1y < 0:
            _add(
                f"Moderate long-term downtrend ({r1y:+.1f}% 1y)",
                f"{display_name} is negative on a 1-year basis, drifting lower.",
                "BEARISH", r1y, 1, "trend",
            )
        else:
            _add(
                f"Long-term trend flat ({r1y:+.1f}% 1y)",
                "Near-zero 1-year return — range-bound, awaiting a catalyst.",
                "NEUTRAL", r1y, 1, "trend",
            )

    # Signal 4: Pullback / counter-trend
    if r1d is not None and r1y is not None:
        if r1d < -1 and r1y > 15:
            _add(
                f"Pullback in strong uptrend (today {r1d:+.2f}%, 1y {r1y:+.1f}%)",
                "Today's dip in an otherwise strong uptrend is a potential mean-reversion setup.",
                "BULLISH", r1d, 2, "mean_reversion",
            )
        elif r1d > 1 and r1y < -15:
            _add(
                f"Counter-trend bounce in downtrend (today {r1d:+.2f}%, 1y {r1y:+.1f}%)",
                "A single-day rally in a structurally weak environment is likely a technical bounce.",
                "BEARISH", r1d, 2, "mean_reversion",
            )

    # Signal 5: Relative strength in universe
    top_decile = max(1, universe_size // 10)
    top_third = max(1, universe_size // 3)
    bot_decile = universe_size - top_decile
    bot_third = universe_size - top_third

    if rank_1d <= top_decile:
        _add(
            f"Top-decile relative strength today (rank #{rank_1d} of {universe_size})",
            f"{display_name} is in the top 10% of the universe by today's return.",
            "BULLISH", float(rank_1d), 2, "relative_strength",
        )
    elif rank_1d <= top_third:
        _add(
            f"Above-average relative strength today (rank #{rank_1d} of {universe_size})",
            f"{display_name} is outperforming the majority of peers today.",
            "BULLISH", float(rank_1d), 1, "relative_strength",
        )
    elif rank_1d > bot_decile:
        _add(
            f"Bottom-decile relative weakness today (rank #{rank_1d} of {universe_size})",
            f"{display_name} is in the bottom 10% of the universe — active underperformance.",
            "BEARISH", float(rank_1d), 2, "relative_strength",
        )
    elif rank_1d > bot_third:
        _add(
            f"Below-average relative strength today (rank #{rank_1d} of {universe_size})",
            f"{display_name} is lagging most peers today.",
            "BEARISH", float(rank_1d), 1, "relative_strength",
        )

    # Signal 6: 6-month momentum
    if r6m is not None:
        if r6m > 10:
            _add(
                f"6-month momentum strong ({r6m:+.1f}%)",
                f"A {r6m:+.1f}% half-year return shows sustained strength across two quarters.",
                "BULLISH", r6m, 1, "momentum",
            )
        elif r6m < -10:
            _add(
                f"6-month momentum weak ({r6m:+.1f}%)",
                f"A {r6m:+.1f}% six-month loss indicates weakness persisting across two quarters.",
                "BEARISH", r6m, 1, "momentum",
            )

    # Confluence score
    mw = max_weight[0]
    if mw > 0:
        raw_score = (weighted_bull[0] - weighted_bear[0]) / mw
    else:
        raw_score = 0.0

    confluence_score = round(raw_score, 4)

    abs_score = abs(confluence_score)
    confidence_label = "HIGH" if abs_score >= 0.55 else ("MEDIUM" if abs_score >= 0.25 else "LOW")

    bull_count = sum(1 for s in signals if s["strength"] == "BULLISH")
    bear_count = sum(1 for s in signals if s["strength"] == "BEARISH")

    if confluence_score >= _BUY_SCORE_THRESHOLD and bull_count >= _BUY_MIN_SIGNALS:
        action = "BUY"
    elif confluence_score <= _SELL_SCORE_THRESHOLD and bear_count >= _SELL_MIN_SIGNALS:
        action = "SELL"
    else:
        action = "HOLD"

    logger.debug(
        "relative_strength: %s score=%.3f action=%s rank=%d/%d",
        symbol, confluence_score, action, rank_1d, universe_size,
    )

    return RelativeStrengthResult(
        symbol=symbol,
        action=action,
        confluence_score=confluence_score,
        confidence_label=confidence_label,
        bull_count=bull_count,
        bear_count=bear_count,
        rank_in_universe=rank_1d,
        universe_size=universe_size,
        signals=signals,
        returns=returns,
    )


def rank_universe(
    returns_by_symbol: dict[str, dict[str, float | None]],
    names: dict[str, str] | None = None,
) -> list[RelativeStrengthResult]:
    """Score and rank an entire universe of symbols by confluence score.

    Args:
        returns_by_symbol: Map of symbol → returns dict (keys: 1d, 1w, 1m, 3m, 6m, 1y).
        names: Optional map of symbol → display name.

    Returns:
        List of RelativeStrengthResult sorted BUY first, then HOLD, then SELL;
        within each group sorted by confluence_score descending.
    """
    if names is None:
        names = {}

    # Rank all symbols by 1d return for relative strength signal
    with_1d = [
        (sym, (rets.get("1d") or 0.0))
        for sym, rets in returns_by_symbol.items()
    ]
    sorted_by_1d = sorted(with_1d, key=lambda x: x[1], reverse=True)
    rank_map = {sym: rank + 1 for rank, (sym, _) in enumerate(sorted_by_1d)}
    total = len(returns_by_symbol)

    results: list[RelativeStrengthResult] = []
    for symbol, returns in returns_by_symbol.items():
        rank_1d = rank_map.get(symbol, total)
        result = score_symbol(
            symbol=symbol,
            returns=returns,
            rank_1d=rank_1d,
            universe_size=total,
            name=names.get(symbol),
        )
        results.append(result)

    results.sort(
        key=lambda r: (_ACTION_ORDER.get(r.action, 1), -r.confluence_score)
    )

    logger.info(
        "rank_universe: %d symbols scored, buys=%d holds=%d sells=%d",
        total,
        sum(1 for r in results if r.action == "BUY"),
        sum(1 for r in results if r.action == "HOLD"),
        sum(1 for r in results if r.action == "SELL"),
    )

    return results
