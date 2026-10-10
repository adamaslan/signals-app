"""Swing lab — triggers, trade simulation, forward returns, grid, CLI. Offline."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from signals_app.swing import (
    ExitRule,
    ForwardSpec,
    GridSpec,
    conditional_forward,
    dip,
    dip_entries,
    forward_returns,
    parse_trigger,
    query,
    run_forward,
    run_grid,
    runup,
    simulate,
    summarize,
)


def _sawtooth(cycles: int = 10) -> np.ndarray:
    """Flat 100, slide to 89 over 5 bars, recover to 100 in 3, repeat."""
    one = [100.0] * 10 + [97.0, 94.0, 91.0, 90.0, 89.0, 92.0, 96.0, 100.0]
    return np.array(one * cycles + [100.0] * 30)


class TestTriggers:
    def test_dip_marks_bars_below_threshold(self) -> None:
        mask = dip(10, 5)(_sawtooth(1))
        assert list(np.flatnonzero(mask)) == [11, 12, 13, 14, 15]

    def test_runup_from_low(self) -> None:
        close = np.array([10.0, 10, 10, 11, 13])
        assert list(runup(3, 25)(close)) == [False, False, False, False, True]

    def test_no_lookahead_before_window_fills(self) -> None:
        assert not dip(10, 1)(np.linspace(100, 50, 9)).any()

    def test_expression_parser_and_or(self) -> None:
        close = _sawtooth(2)
        both = parse_trigger("dip(10, 5) & dip(10, 10)")(close)
        either = parse_trigger("dip(10,10) | runup(5, 5)")(close)
        assert (both == dip(10, 10)(close)).all()
        assert either.sum() > both.sum()

    @pytest.mark.parametrize("bad", ["", "dip", "nope(1,2)", "dip(1,5)", "dip(5,5,5,5)"])
    def test_bad_expressions(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_trigger(bad)


class TestTrades:
    def test_take_profit_exit(self) -> None:
        close = _sawtooth()
        trades = simulate(close, dip(10, 5)(close), ExitRule(take_profit_pct=5, max_hold=20))
        assert len(trades) == 10
        assert trades[0].entry_price == 94.0 and trades[0].exit_price == 100.0
        assert trades[0].exit_reason == "take_profit"

    def test_stop_exit_and_no_overlap(self) -> None:
        close = _sawtooth()
        trades = simulate(close, dip(10, 5)(close), ExitRule(stop_pct=4, max_hold=20))
        assert trades[0].exit_reason == "stop" and trades[0].exit_price == 90.0
        assert all(b.entry_idx > a.exit_idx for a, b in zip(trades, trades[1:], strict=False))

    def test_summary_and_robustness(self) -> None:
        close = _sawtooth()
        trades = simulate(close, dip(10, 5)(close), ExitRule(take_profit_pct=5, max_hold=20))
        stats = summarize(trades, len(close))
        assert stats.win_rate == 1.0
        assert stats.avg_pct == pytest.approx((100 / 94 - 1) * 100)
        assert stats.robust

    def test_exit_rule_validation(self) -> None:
        with pytest.raises(ValueError):
            ExitRule(max_hold=0)
        with pytest.raises(ValueError):
            ExitRule(take_profit_pct=-1)


class TestForward:
    def test_forward_returns(self) -> None:
        fwd = forward_returns(np.array([100.0, 110, 121]), 1)
        assert fwd[:2] == pytest.approx([10.0, 10.0]) and np.isnan(fwd[2])

    def test_conditional_vs_baseline(self) -> None:
        close = _sawtooth()
        stats = conditional_forward(close, dip(10, 5)(close), 3)
        assert stats.n_bars > 0
        assert stats.edge_pct is not None and stats.edge_pct > 0


class TestGrid:
    def test_grid_and_query(self) -> None:
        spec = GridSpec(
            entries=dip_entries((10,), (5, 8)),
            take_profits=(5, None), stops=(None, 4), max_holds=(20,),
        )
        df = run_grid({"SAW": _sawtooth()}, spec)
        assert set(df.entry) == {"dip(10,5)", "dip(10,8)"}
        best = query(df, "robust and n >= 10", sort="avg_pct", top=1)
        assert len(best) == 1
        assert best.loc[0, "avg_pct"] == df[df.robust & (df.n >= 10)].avg_pct.max()

    def test_forward_table(self) -> None:
        df = run_forward({"SAW": _sawtooth()}, ForwardSpec({"dip": dip(10, 5)}, horizons=(3, 5)))
        assert list(df.horizon) == [3, 5]

    def test_bad_sort_column(self) -> None:
        with pytest.raises(ValueError, match="unknown sort column"):
            query(pd.DataFrame({"a": [1]}), sort="b")


class TestCli:
    @pytest.fixture(autouse=True)
    def _fake_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from signals_app.data import fetcher

        def fake(self, symbol: str, period: str = "10y") -> pd.DataFrame:
            close = _sawtooth()
            idx = pd.bdate_range("2024-01-01", periods=len(close))
            return pd.DataFrame({"Close": close}, index=idx)

        monkeypatch.setattr(fetcher.DataFetcher, "fetch_daily_history", fake)

    def _run(self, *args: str) -> object:
        from signals_app.cli.main import app

        return CliRunner().invoke(app, ["swing", *args])

    def test_grid_json(self) -> None:
        r = self._run("grid", "SAW", "--windows", "10", "--dips", "5", "--tps", "5,none",
                      "--stops", "none", "--holds", "20", "--json", "--where", "n >= 10")
        assert r.exit_code == 0, r.output
        rows = json.loads(r.stdout.strip().splitlines()[-1])
        assert rows and rows[0]["entry"] == "dip(10,5)"

    def test_test_command_lists_trades(self) -> None:
        r = self._run("test", "SAW", "--entry", "dip(10,5)", "--tp", "5", "--trades", "--json")
        assert r.exit_code == 0, r.output
        payload = json.loads(r.stdout.strip().splitlines()[-1])
        assert payload["stats"]["n"] == 10 and len(payload["trades"]) == 10

    def test_forward_and_levels(self) -> None:
        assert self._run("forward", "SAW", "--when", "dip(10,5)", "--json").exit_code == 0
        r = self._run("levels", "SAW", "--windows", "10", "--dips", "5", "--json")
        assert json.loads(r.stdout.strip().splitlines()[-1])[0]["dip5_at"] == pytest.approx(95.0)

    def test_bad_expression_is_usage_error(self) -> None:
        assert self._run("test", "SAW", "--entry", "nope(1)").exit_code == 2
