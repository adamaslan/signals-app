"""scripts/graded_shadow.py: the comparison, the cutover gate and the K fit."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import graded_shadow as gs  # noqa: E402

from signals_app.detection.base import MutableSignal  # noqa: E402
from signals_app.scoring.evidence import EvidenceTable  # noqa: E402
from signals_app.scoring.kinds import stamp_kinds  # noqa: E402


def _row(old: str, new: str, fwd: float, old_score: float = 0.0, new_score: float = 0.0) -> dict:
    return {"ticker": "A", "bar_ts": "2026-10-07T00:00:00+00:00", "fwd": fwd, "old_action": old,
            "new_action": new, "old_score": old_score, "new_score": new_score, "drivers": []}


class TestRankerStats:
    def test_buy_and_sell_hit_rates(self) -> None:
        rows = [_row("BUY", "HOLD", 0.1), _row("BUY", "HOLD", -0.1), _row("SELL", "HOLD", -0.2),
                _row("HOLD", "HOLD", 0.0)]
        stats = gs.ranker_stats(rows, "old_action", "old_score")
        assert stats["buy"] == {"n": 2, "hit_rate": 0.5}
        assert stats["sell"] == {"n": 1, "hit_rate": 1.0}

    def test_no_calls_gives_none_not_a_crash(self) -> None:
        stats = gs.ranker_stats([_row("HOLD", "HOLD", 0.1)] * 5, "old_action", "old_score")
        assert stats["buy"] == {"n": 0, "hit_rate": None}

    def test_spearman_is_positive_when_score_tracks_return(self) -> None:
        rows = [_row("HOLD", "HOLD", i / 100, old_score=i / 100) for i in range(30)]
        assert gs.ranker_stats(rows, "old_action", "old_score")["spearman"] == 1.0

    def test_too_few_rows_for_a_correlation(self) -> None:
        assert gs.ranker_stats([_row("BUY", "BUY", 0.1)], "old_action", "old_score")["spearman"] is None


class TestFlips:
    def test_counts_each_transition_and_ranks_by_score_change(self) -> None:
        rows = [_row("BUY", "HOLD", 0.0, 0.4, 0.1), _row("BUY", "HOLD", 0.0, 0.4, 0.3),
                _row("HOLD", "BUY", 0.0, 0.1, 0.5), _row("HOLD", "HOLD", 0.0)]
        table = gs.flip_table(rows)
        assert table["total"] == 3 and table["of"] == 4
        assert table["counts"] == {"BUY->HOLD": 2, "HOLD->BUY": 1}
        assert table["examples"][0]["new_score"] == 0.5  # largest |change|: 0.4

    def test_examples_are_bounded(self) -> None:
        rows = [_row("BUY", "HOLD", 0.0, 0.5, 0.1)] * 100
        assert len(gs.flip_table(rows)["examples"]) == gs.MAX_FLIP_EXAMPLES


class TestCutoverGate:
    GOOD = {"buy": {"n": 400, "hit_rate": 0.56}, "spearman": 0.05}
    BASE = {"buy": {"n": 500, "hit_rate": 0.54}, "spearman": 0.04}

    def test_ready_when_every_criterion_holds(self) -> None:
        assert gs.cutover_check(self.BASE, self.GOOD) == {"ready": True, "reasons": []}

    @pytest.mark.parametrize(
        ("graded", "fragment"),
        [({"buy": {"n": 299, "hit_rate": 0.9}, "spearman": 0.5}, "299 graded BUY"),
         ({"buy": {"n": 400, "hit_rate": 0.5}, "spearman": 0.5}, "hit rate"),
         ({"buy": {"n": 400, "hit_rate": 0.9}, "spearman": 0.0}, "rank correlation"),
         ({"buy": {"n": 400, "hit_rate": None}, "spearman": 0.5}, "unavailable")],
    )
    def test_each_criterion_blocks_independently(self, graded: dict, fragment: str) -> None:
        verdict = gs.cutover_check(self.BASE, graded)
        assert not verdict["ready"]
        assert any(fragment in reason for reason in verdict["reasons"])

    def test_compare_reports_a_blocked_cutover_on_a_tiny_sample(self) -> None:
        report = gs.compare([_row("BUY", "BUY", 0.1, 0.3, 0.3)] * 10)
        assert report["cutover"]["ready"] is False
        assert report["bars"] == 10


class TestJoin:
    def test_joins_on_ticker_and_instant_across_timestamp_spellings(self) -> None:
        shadow = [{"ticker": "A", "bar_ts": "2026-10-07T00:00:00+00:00", "action": "BUY", "score": 0.3,
                   "payload": {"production": {"action": "HOLD", "score": 0.1}, "drivers": []}}]
        forward = [{"ticker": "A", "bar_ts": "2026-10-07T00:00:00Z", "pct_return": 0.04}]
        (row,) = gs.join_rows(shadow, forward)
        assert (row["old_action"], row["new_action"], row["fwd"]) == ("HOLD", "BUY", 0.04)

    def test_drops_rows_without_a_return_or_a_production_call(self) -> None:
        shadow = [{"ticker": "A", "bar_ts": "2026-10-07T00:00:00Z", "action": "BUY", "score": 0.3,
                   "payload": {"production": {"action": "HOLD", "score": 0.1}}},
                  {"ticker": "B", "bar_ts": "2026-10-07T00:00:00Z", "action": "BUY", "score": 0.3,
                   "payload": {}}]
        forward = [{"ticker": "B", "bar_ts": "2026-10-07T00:00:00Z", "pct_return": 0.04}]
        assert gs.join_rows(shadow, forward) == []


def _sig(label: str, strength: str, category: str) -> MutableSignal:
    sig = MutableSignal(signal=label, description="", strength=strength, category=category)
    stamp_kinds([sig])
    return sig


_BULL_STATES = [("MA ALIGNMENT BULLISH", "BULLISH", "MA_TREND"), ("STRONG UPTREND", "BULLISH", "ADX"),
                ("RSI OVERSOLD", "BULLISH", "RSI"), ("STOCHASTIC OVERSOLD", "BULLISH", "STOCHASTIC"),
                ("CMF STRONG BUYING", "BULLISH", "OBV_CMF"),
                ("OBV BULLISH DIVERGENCE", "BULLISH", "OBV_CMF")]
_BEAR_STATES = [("MA ALIGNMENT BEARISH", "BEARISH", "MA_TREND"), ("STRONG DOWNTREND", "BEARISH", "ADX"),
                ("RSI14 OVERBOUGHT (>70)", "BEARISH", "RSI"),
                ("STOCHASTIC OVERBOUGHT", "BEARISH", "STOCHASTIC"),
                ("CMF STRONG SELLING", "BEARISH", "OBV_CMF"),
                ("OBV BEARISH DIVERGENCE", "BEARISH", "OBV_CMF")]


def _planted_bars(events_predict: bool, n: int = 800, seed: int = 0) -> list[gs.FitBar]:
    """Every bar: one MACD-cross event plus 0-6 state signals that all point one way.

    Exactly one group tracks the forward return; the other is a coin flip. The number
    of states varies per bar on purpose: rank correlation only sees ordering, so a
    fixed count would make every state weight rank the bars identically."""
    rng = np.random.default_rng(seed)
    bars = []
    for i in range(n):
        fwd = float(rng.normal(0, 0.05))
        truth = fwd > 0
        event_up = truth if events_predict else bool(rng.random() < 0.5)
        state_up = bool(rng.random() < 0.5) if events_predict else truth
        event = (_sig("MACD BULL CROSS", "BULLISH", "MACD") if event_up
                 else _sig("MACD BEAR CROSS", "BEARISH", "MACD"))
        pool = _BULL_STATES if state_up else _BEAR_STATES
        picks = rng.permutation(len(pool))[: int(rng.integers(0, 7))]
        states = [_sig(*pool[j]) for j in picks]
        bars.append(gs.FitBar([event, *states], fwd, is_fit=i < int(n * 0.7)))
    return bars


class TestFitKinds:
    EVIDENCE = EvidenceTable(version="t")

    def test_predictive_events_and_noisy_states_push_the_state_weight_down(self) -> None:
        result = gs.fit_kind_multipliers(_planted_bars(events_predict=True), self.EVIDENCE)
        assert result["K"]["S"] == min(gs.K_GRID["S"])
        assert result["holdout_spearman"] > 0.3

    def test_predictive_states_and_noisy_events_push_the_state_weight_up(self) -> None:
        result = gs.fit_kind_multipliers(_planted_bars(events_predict=False), self.EVIDENCE)
        assert result["K"]["S"] == max(gs.K_GRID["S"])

    def test_x_and_context_are_never_moved(self) -> None:
        result = gs.fit_kind_multipliers(_planted_bars(events_predict=True), self.EVIDENCE)
        assert result["K"]["X"] == 1.0 and result["K"]["C"] == 0.0

    def test_reports_the_start_k_beside_the_fit_for_overfit_checks(self) -> None:
        result = gs.fit_kind_multipliers(_planted_bars(events_predict=True), self.EVIDENCE)
        assert {"start_K", "start_fit_spearman", "start_holdout_spearman", "fit_bars",
                "holdout_bars"} <= set(result)
        assert result["fit_bars"] + result["holdout_bars"] == 800

    def test_never_picks_a_candidate_worse_than_the_start_on_the_fit_set(self) -> None:
        result = gs.fit_kind_multipliers(_planted_bars(events_predict=True), self.EVIDENCE)
        assert result["fit_spearman"] >= result["start_fit_spearman"]
