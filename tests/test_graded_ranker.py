"""P6 GradedConfluenceRanker: the pipeline steps, one behavior per test."""
from __future__ import annotations

import pytest

from signals_app.detection.base import MutableSignal
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker, FamilyConfluenceRanker
from signals_app.scoring.evidence import EvidenceTable, load_evidence
from signals_app.scoring.graded import (
    EVENT_DECAY_BY_AGE,
    STATE_BUDGET_PER_SIDE,
    VOLUME_AMPLIFIER,
    GradedConfluenceRanker,
)
from signals_app.scoring.kinds import stamp_kinds
from signals_app.scoring.regime import TREND_UP

from .test_kinds import _random_walk_ohlcv

OPEN_EVIDENCE = EvidenceTable(version="test-open")  # default E = 1.0, nothing zeroed


def _sig(label: str, strength: str, category: str) -> MutableSignal:
    sig = MutableSignal(signal=label, description="", strength=strength, category=category)
    stamp_kinds([sig])
    return sig


def _ranker(evidence: EvidenceTable = OPEN_EVIDENCE) -> GradedConfluenceRanker:
    return GradedConfluenceRanker(evidence)


# One bullish *state* in each of the five families.
def _states_in_every_family() -> list[MutableSignal]:
    return [
        _sig("MA ALIGNMENT BULLISH", "BULLISH", "MA_TREND"),
        _sig("STRONG UPTREND", "BULLISH", "ADX"),
        _sig("RSI OVERSOLD", "BULLISH", "RSI"),
        _sig("STOCHASTIC OVERSOLD", "BULLISH", "STOCHASTIC"),
        _sig("BB(20,2.0) RIDING UPPER BAND", "STRONG BULLISH", "BB_BREAKOUT"),
        _sig("CMF STRONG BUYING", "BULLISH", "OBV_CMF"),
        _sig("OBV BULLISH DIVERGENCE", "BULLISH", "OBV_CMF"),
    ]


class TestStatesCannotCreateAnAction:
    def test_states_alone_never_buy_however_many_agree(self) -> None:
        result = _ranker().rank_signals(_states_in_every_family())
        assert result.score > 0.0
        assert result.action == "HOLD"
        assert result.live_events == 0

    def test_the_same_states_plus_one_live_event_can_buy(self) -> None:
        signals = _states_in_every_family() + [
            _sig("MACD BULL CROSS", "BULLISH", "MACD"),
            _sig("GOLDEN CROSS", "STRONG BULLISH", "MA_CROSS"),
            _sig("CMF CROSSED POSITIVE", "BULLISH", "OBV_CMF"),
        ]
        result = _ranker().rank_signals(signals)
        assert result.action == "BUY"
        assert result.live_events == 3

    def test_bearish_mirror_sells_only_with_an_event(self) -> None:
        states = [
            _sig("MA ALIGNMENT BEARISH", "BEARISH", "MA_TREND"),
            _sig("STRONG DOWNTREND", "BEARISH", "ADX"),
            _sig("RSI14 OVERBOUGHT (>70)", "BEARISH", "RSI"),
            _sig("STOCHASTIC OVERBOUGHT", "BEARISH", "STOCHASTIC"),
            _sig("CMF STRONG SELLING", "BEARISH", "OBV_CMF"),
        ]
        assert _ranker().rank_signals(states).action == "HOLD"
        events = states + [
            _sig("DEATH CROSS", "STRONG BEARISH", "MA_CROSS"),
            _sig("MACD BEAR CROSS", "BEARISH", "MACD"),
            _sig("CMF CROSSED NEGATIVE", "BEARISH", "OBV_CMF"),
        ]
        assert _ranker().rank_signals(events).action == "SELL"


class TestPoints:
    def test_event_points_match_the_formula(self) -> None:
        # MACD cross: base 1 x K 1.0 + MACD bonus 0.5, E 1 -> 1.5
        result = _ranker().rank_signals([_sig("MACD BULL CROSS", "BULLISH", "MACD")])
        assert result.bull_weight == 1.5

    def test_state_points_are_a_quarter_of_the_old_vote(self) -> None:
        result = _ranker().rank_signals([_sig("STRONG UPTREND", "BULLISH", "ADX")])
        assert result.bull_weight == 0.25

    def test_threshold_event_uses_the_lower_multiplier(self) -> None:
        # LARGE GAIN: STRONG (2) x K 0.6 = 1.2; PRICE_ACTION has no category bonus
        result = _ranker().rank_signals([_sig("LARGE GAIN", "STRONG BULLISH", "PRICE_ACTION")])
        assert result.bull_weight == pytest.approx(1.2)

    def test_proximity_is_the_lightest_vote(self) -> None:
        result = _ranker().rank_signals([_sig("AT LOWER BB", "BULLISH", "BOLLINGER")])
        assert result.bull_weight == pytest.approx(0.15)

    def test_evidence_scales_points(self) -> None:
        half = EvidenceTable(version="t", labels={"MACD BULL CROSS": 0.5})
        result = _ranker(half).rank_signals([_sig("MACD BULL CROSS", "BULLISH", "MACD")])
        assert result.bull_weight == 0.75

    def test_a_standing_state_no_longer_outvotes_the_event_it_should_trail(self) -> None:
        state = _ranker().rank_signals([_sig("MA ALIGNMENT BULLISH", "STRONG BULLISH", "MA_TREND")])
        event = _ranker().rank_signals([_sig("MACD BULL CROSS", "BULLISH", "MACD")])
        assert state.bull_weight < event.bull_weight


class TestCollapse:
    def test_a_fact_reported_twice_votes_once(self) -> None:
        golden = _sig("GOLDEN CROSS", "STRONG BULLISH", "MA_CROSS")
        once = _ranker().rank_signals([golden])
        twice = _ranker().rank_signals([golden, golden.model_copy()])
        assert twice.bull_count == 1
        assert once.bull_weight == 2.5
        assert once.bull_weight < twice.bull_weight <= once.bull_weight + 0.5

    def test_three_macd_parameter_sets_cast_one_vote_plus_a_capped_bonus(self) -> None:
        crosses = [_sig(f"MACD({p}) BULL CROSS", "BULLISH", "MACD")
                   for p in ("10,20,5", "19,39,9", "20,50,10")]
        result = _ranker().rank_signals(crosses)
        assert result.bull_count == 1
        assert result.bull_weight == pytest.approx(1.5 + 0.3)

    def test_the_bonus_is_capped(self) -> None:
        many = [_sig(f"MACD({i},26,9) BULL CROSS", "BULLISH", "MACD") for i in range(12)]
        assert _ranker().rank_signals(many).bull_weight == pytest.approx(1.5 + 0.5)

    def test_opposite_sides_of_one_concept_do_not_collapse(self) -> None:
        both = [_sig("MACD BULL CROSS", "BULLISH", "MACD"), _sig("MACD BEAR CROSS", "BEARISH", "MACD")]
        result = _ranker().rank_signals(both)
        assert (result.bull_count, result.bear_count) == (1, 1)

    def test_different_concepts_in_one_family_both_vote(self) -> None:
        both = [_sig("MACD BULL CROSS", "BULLISH", "MACD"),
                _sig("RSI14 CROSSED 50 BULL", "BULLISH", "RSI")]
        assert _ranker().rank_signals(both).bull_count == 2


class TestStateBudget:
    def test_ten_states_in_a_family_are_capped(self) -> None:
        many = [_sig(f"RSI{p} OVERSOLD (<30)", "STRONG BULLISH", "RSI") for p in range(10)]
        many += [_sig("STOCHASTIC OVERSOLD", "STRONG BULLISH", "STOCHASTIC"),
                 _sig("RSI EXTREME OVERSOLD", "STRONG BULLISH", "RSI")]
        result = _ranker().rank_signals(many)
        assert result.bull_weight <= STATE_BUDGET_PER_SIDE + 1e-9

    def test_events_are_not_part_of_the_state_budget(self) -> None:
        events = [_sig("MACD BULL CROSS", "BULLISH", "MACD"),
                  _sig("RSI14 CROSSED 50 BULL", "BULLISH", "RSI")]
        assert _ranker().rank_signals(events).bull_weight > STATE_BUDGET_PER_SIDE


class TestEvidenceZeroIsInert:
    def test_a_flag_only_event_changes_neither_score_nor_action(self) -> None:
        base = _states_in_every_family() + [_sig("MACD BULL CROSS", "BULLISH", "MACD")]
        flag_only = _sig("KUMO BREAKOUT", "BULLISH", "ICHIMOKU")
        evidence = load_evidence()
        with_it = GradedConfluenceRanker(evidence).rank_signals(base + [flag_only])
        without = GradedConfluenceRanker(evidence).rank_signals(base)
        assert with_it.score == without.score
        assert with_it.bull_weight == without.bull_weight
        assert with_it.drivers == without.drivers
        assert "KUMO BREAKOUT" in with_it.flag_only
        assert "KUMO BREAKOUT" not in without.flag_only

    def test_an_unearned_event_alone_cannot_satisfy_the_event_gate(self) -> None:
        signals = _states_in_every_family() + [_sig("KUMO BREAKOUT", "BULLISH", "ICHIMOKU")]
        result = GradedConfluenceRanker(load_evidence()).rank_signals(signals)
        assert result.live_events == 0
        assert result.action == "HOLD"

    def test_zero_evidence_zeroes_the_category_bonus_too(self) -> None:
        zero = EvidenceTable(version="t", labels={"MACD BULL CROSS": 0.0})
        assert _ranker(zero).rank_signals([_sig("MACD BULL CROSS", "BULLISH", "MACD")]).bull_weight == 0


class TestVolumeAmplifier:
    SPIKE = ("VOLUME SPIKE >2x (MA20)", "SIGNIFICANT", "VOLUME")

    def test_spike_amplifies_the_bars_events_once(self) -> None:
        events = [_sig("MACD BULL CROSS", "BULLISH", "MACD"),
                  _sig("RSI14 CROSSED 50 BULL", "BULLISH", "RSI")]
        plain = _ranker().rank_signals(events)
        spiked = _ranker().rank_signals(events + [_sig(*self.SPIKE), _sig("VOLUME SPIKE >3x (MA10)",
                                                                           "SIGNIFICANT", "VOLUME")])
        assert spiked.bull_weight == pytest.approx(plain.bull_weight * VOLUME_AMPLIFIER)

    def test_spike_does_nothing_for_states_alone(self) -> None:
        states = [_sig("STRONG UPTREND", "BULLISH", "ADX")]
        spiked = _ranker().rank_signals(states + [_sig(*self.SPIKE)])
        assert spiked.bull_weight == _ranker().rank_signals(states).bull_weight

    def test_a_spike_has_no_direction_of_its_own(self) -> None:
        result = _ranker().rank_signals([_sig(*self.SPIKE)])
        assert (result.bull_count, result.bear_count, result.bull_weight) == (0, 0, 0.0)


class TestRegimeGate:
    def test_overbought_is_zeroed_in_an_uptrend(self) -> None:
        sig = _sig("RSI14 OVERBOUGHT (>70)", "BEARISH", "RSI")
        assert _ranker().rank_signals([sig], regime=TREND_UP).bear_weight == 0
        assert _ranker().rank_signals([sig], regime="range").bear_weight > 0

    def test_a_breakdown_is_not_zeroed_in_an_uptrend(self) -> None:
        sig = _sig("BELOW LOWER BB(20,2.0)", "EXTREME BEARISH", "BB_BREAKOUT")
        assert _ranker().rank_signals([sig], regime=TREND_UP).bear_weight == pytest.approx(1.8)

    def test_regime_conditional_evidence_is_used(self) -> None:
        table = EvidenceTable(version="t", labels={"MACD BULL CROSS": 1.0},
                              regimes={"MACD BULL CROSS": {"range": 0.0}})
        sig = _sig("MACD BULL CROSS", "BULLISH", "MACD")
        assert _ranker(table).rank_signals([sig], regime="range").bull_weight == 0
        assert _ranker(table).rank_signals([sig], regime=TREND_UP).bull_weight == 1.5


class TestDecay:
    def test_prior_bar_events_count_at_reduced_weight(self) -> None:
        now = [_sig("RSI14 CROSSED 50 BULL", "BULLISH", "RSI")]
        yesterday = [[_sig("MACD BULL CROSS", "BULLISH", "MACD")]]
        result = _ranker().rank_signals(now, prior_bars=yesterday)
        assert result.bull_weight == pytest.approx(1.0 + 1.5 * EVENT_DECAY_BY_AGE[0])

    def test_two_bars_back_decays_further(self) -> None:
        macd = _sig("MACD BULL CROSS", "BULLISH", "MACD")
        result = _ranker().rank_signals([], prior_bars=[[], [macd]])
        assert result.bull_weight == pytest.approx(1.5 * EVENT_DECAY_BY_AGE[1])

    def test_a_concept_already_firing_now_is_not_counted_again(self) -> None:
        macd = _sig("MACD BULL CROSS", "BULLISH", "MACD")
        result = _ranker().rank_signals([macd], prior_bars=[[macd.model_copy()]])
        assert result.bull_weight == 1.5

    def test_prior_states_never_carry_forward(self) -> None:
        state = _sig("STRONG UPTREND", "BULLISH", "ADX")
        assert _ranker().rank_signals([], prior_bars=[[state]]).bull_weight == 0

    def test_decay_is_off_by_default(self) -> None:
        assert _ranker().rank_signals([]).bull_weight == 0


class TestContractAndPurity:
    def test_empty_input_is_a_clean_hold(self) -> None:
        result = _ranker().rank_signals([])
        assert (result.action, result.score, result.total_signals) == ("HOLD", 0.0, 0)

    def test_unclassified_signals_are_counted_not_voted(self) -> None:
        unknown = MutableSignal(signal="MYSTERY", description="", strength="STRONG BULLISH",
                                category="TREND")
        result = _ranker().rank_signals([unknown])
        assert result.unclassified == 1
        assert result.bull_weight == 0

    def test_deterministic_and_does_not_mutate_input(self) -> None:
        signals = _states_in_every_family() + [_sig("MACD BULL CROSS", "BULLISH", "MACD")]
        snapshot = [s.model_dump() for s in signals]
        first = _ranker().rank_signals(signals)
        second = _ranker().rank_signals(signals)
        assert first == second
        assert [s.model_dump() for s in signals] == snapshot

    def test_score_stays_inside_the_unit_interval(self) -> None:
        extreme = [_sig(f"MACD({i},26,9) BULL CROSS", "EXTREME BULLISH", "MACD") for i in range(50)]
        assert -1.0 <= _ranker().rank_signals(extreme).score <= 1.0

    def test_to_dict_extends_the_base_payload(self) -> None:
        payload = _ranker().rank_signals([_sig("MACD BULL CROSS", "BULLISH", "MACD")]).to_dict()
        assert {"score", "action", "families"} <= set(payload)
        assert {"events", "states", "proximity", "drivers", "ranker_version",
                "evidence_version"} <= set(payload)
        assert payload["ranker_version"] == "graded-1"
        assert payload["evidence_version"] == "test-open"

    def test_drivers_are_the_top_votes_by_points(self) -> None:
        result = _ranker().rank_signals(_states_in_every_family() + [
            _sig("GOLDEN CROSS", "STRONG BULLISH", "MA_CROSS")])
        assert result.drivers[0]["signal"] == "GOLDEN CROSS"
        assert len(result.drivers) <= 5
        points = [d["points"] for d in result.drivers]
        assert points == sorted(points, reverse=True)

    def test_kind_counts_describe_what_fired(self) -> None:
        result = _ranker().rank_signals([
            _sig("MACD BULL CROSS", "BULLISH", "MACD"),
            _sig("STRONG UPTREND", "BULLISH", "ADX"),
            _sig("AT LOWER BB", "BULLISH", "BOLLINGER")])
        assert (result.events, result.states, result.proximity) == (1, 1, 1)


class TestExistingRankersAreUntouched:
    def test_running_the_graded_ranker_changes_nothing_for_the_old_ones(self) -> None:
        full = compute_indicators(_random_walk_ohlcv(4))
        graded = GradedConfluenceRanker()
        for end in range(230, len(full) + 1, 40):
            signals = list(detect_all_signals(full.iloc[:end]))
            before = (ConfluenceRanker().rank_signals(signals),
                      FamilyConfluenceRanker().rank_signals(signals))
            graded.rank_signals(signals, df=full.iloc[:end])
            after = (ConfluenceRanker().rank_signals(signals),
                     FamilyConfluenceRanker().rank_signals(signals))
            assert before == after

    def test_runs_on_real_detector_output_with_context(self) -> None:
        full = compute_indicators(_random_walk_ohlcv(7))
        signals = list(detect_all_signals(full, include_experimental=True))
        result = GradedConfluenceRanker().rank_signals(signals, regime="range", df=full)
        assert result.risk_context is not None and result.location is not None
        assert result.unclassified == 0
        assert -1.0 <= result.score <= 1.0
