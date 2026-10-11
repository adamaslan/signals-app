"""The ``swing_grid`` chain op — offline, synthetic closes."""
from __future__ import annotations

import pandas as pd
import pytest

from signals_app.chains import ChainError, ChainRunner, ChainStep
from signals_app.service import BatchResult


def _frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes}, index=pd.bdate_range("2023-01-02", periods=len(closes)))


def _sawtooth(cycles: int = 16) -> list[float]:
    """Drop 10% over 3 bars, bounce back — a dip-buy that always pays."""
    one = [100.0] * 10 + [97.0, 94.0, 91.0, 90.0, 94.0, 98.0, 102.0, 105.0, 100.0]
    return one * cycles


def _runner(series: dict[str, list[float]]) -> ChainRunner:
    async def analyze_many(symbols, period, *, no_llm):  # noqa: ANN001, ANN202
        return BatchResult(ok=[], failed=[])

    def fetch_daily(symbol: str, period: str) -> pd.DataFrame:
        if symbol not in series:
            raise ValueError(f"{symbol}: no data")
        return _frame(series[symbol])

    return ChainRunner(fetch_daily=fetch_daily, analyze_many=analyze_many, holdem=None)


_PARAMS = {
    "windows": [10], "dip_pcts": [5], "take_profits": [5, None], "stops": [None],
    "max_holds": [10], "min_trades": 5,
}


class TestSwingGridOp:
    async def test_attaches_ranked_best_rows(self) -> None:
        result = await _runner({"AAA": _sawtooth()}).run(
            [ChainStep("symbols", {"symbols": ["aaa"]}), ChainStep("swing_grid", _PARAMS)]
        )
        swing = result.rows["AAA"]["swing"]
        assert swing["combos_tested"] == 2
        best = swing["best"]
        assert best and best[0]["avg_pct"] >= best[-1]["avg_pct"]
        assert best[0]["entry"] == "dip(10,5)"
        assert "symbol" not in best[0]

    async def test_drops_symbols_with_no_data_and_says_why(self) -> None:
        result = await _runner({"AAA": _sawtooth()}).run(
            [ChainStep("symbols", {"symbols": ["aaa", "bbb"]}), ChainStep("swing_grid", _PARAMS)]
        )
        assert result.symbols == ["AAA"]
        assert "no data" in result.steps[1].dropped["BBB"]

    async def test_min_best_avg_pct_gates(self) -> None:
        result = await _runner({"AAA": _sawtooth()}).run(
            [
                ChainStep("symbols", {"symbols": ["aaa"]}),
                ChainStep("swing_grid", {**_PARAMS, "min_best_avg_pct": 10_000}),
            ]
        )
        assert result.symbols == []
        assert "below" in result.steps[1].dropped["AAA"]

    @pytest.mark.parametrize(
        "bad",
        [{"sort": "symbol"}, {"nope": 1}, {"take_profits": [-5]}, {"windows": ["x"]}],
    )
    async def test_bad_params_are_chain_errors(self, bad: dict) -> None:
        with pytest.raises(ChainError):
            await _runner({"AAA": _sawtooth()}).run(
                [
                    ChainStep("symbols", {"symbols": ["aaa"]}),
                    ChainStep("swing_grid", {**_PARAMS, **bad}),
                ]
            )

    async def test_combo_cap(self) -> None:
        with pytest.raises(ChainError, match="cap"):
            await _runner({"AAA": _sawtooth()}).run(
                [
                    ChainStep("symbols", {"symbols": ["aaa"]}),
                    ChainStep("swing_grid", {
                        "windows": list(range(2, 40)), "dip_pcts": list(range(1, 30)),
                    }),
                ]
            )
